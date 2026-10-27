"""S0 contract tests: wire models, auth, error format, OpenAPI drift, two-call model port."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from persona_os.api.app import create_app
from persona_os.config import Settings
from persona_os.domain import contract as c
from persona_os.domain.ports import GenerationInput, StateInput
from persona_os.infra.fake_model import FakePersonaModel

ROOT = Path(__file__).resolve().parent.parent
AUTH = {"Authorization": "Bearer t-anima"}


@pytest.fixture
def client():
    settings = Settings(service_tokens=SecretStr("t-anima=anima,t-other=other"),
                        database_url="sqlite+aiosqlite://")
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def entry(**kw):
    base = {"entry_id": "L2_need_autonomy", "layer": "L2", "construct": "need",
            "construct_id": "need.autonomy",
            "value": {"satisfaction": 0.167, "frustration": 0.667, "scale": "0-1"},
            "text": "……", "source": "measurement",
            "source_ref": {"instrument": "DIGITAL_TWIN_PERSONALITY_ASSESSMENT_V1_FOUR_POINT"}}
    return base | kw


EPISODE = {
    "episode_id": "s03-w03-table-01",
    "situation": {"title": "第 3 周", "description": "情境与任务背景"},
    "participants": [
        {"participant_id": "p1", "display_name": "小澄", "persona_id": "student-283",
         "position": {"identity": "组长", "responsibilities": ["推进讨论进度"]}},
        {"participant_id": "p2", "display_name": "阿远", "persona_id": "student-532"},
    ],
}
PREPARE = {
    "action_id": "s03-w03-table-01-t005", "participant_id": "p1", "upto_seq": 13,
    "task": {"instruction": "回应 p2 的理由", "intent": "p2 刚点名你"},
    "output_contract": {"length": {"unit": "words", "soft": [55, 80], "hard": [25, 160]},
                        "extra_schema": {"type": "object"}},
}


def test_openapi_file_is_current():
    from tools.export_openapi import render

    assert (ROOT / "docs" / "openapi.json").read_text(encoding="utf-8") == render(), \
        "run: .venv/bin/python tools/export_openapi.py"


def test_health_needs_no_token(client):
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer nope"},
                                     {"Authorization": "t-anima"}])
def test_unknown_token_is_401_in_contract_format(client, headers):
    r = client.get("/v1/runtime", headers=headers)
    assert r.status_code == 401
    assert r.json() == {"error": {"code": "unauthorized", "retryable": False,
                                  "message": "missing or unknown service token"}}


def test_token_maps_to_namespace(client):
    assert client.get("/v1/runtime", headers=AUTH).json()["namespace"] == "anima"
    other = client.get("/v1/runtime", headers={"Authorization": "Bearer t-other"}).json()
    assert other["namespace"] == "other" and other["calls_per_action"] == 2


def test_validation_error_format(client):
    bad = {"personas": [{"persona_id": "student-1", "entries": [entry(layer="L1")]}]}
    r = client.post("/v1/personas:import", json=bad, headers=AUTH)
    assert r.status_code == 422
    body = r.json()["error"]
    assert body["code"] == "invalid_request" and "does not belong to layer" in body["message"]


def test_entry_rules():
    c.BeliefEntry.model_validate(entry())
    with pytest.raises(ValidationError, match="construct_id must start"):
        c.BeliefEntry.model_validate(entry(construct_id="value.autonomy"))
    with pytest.raises(ValidationError, match="source_ref"):
        c.BeliefEntry.model_validate(entry(source_ref=None))
    with pytest.raises(ValidationError, match="unknown entries"):
        c.BeliefEntry.model_validate(entry(epistemic_status="unknown"))
    with pytest.raises(ValidationError):
        c.BeliefEntry.model_validate(entry(construct="mood", construct_id="mood.x"))
    unknown = c.BeliefEntry.model_validate(entry(entry_id="L2_goal", construct="goal",
                                                 construct_id="goal.semester", value=None,
                                                 source="generated", source_ref=None,
                                                 epistemic_status="unknown"))
    assert unknown.model_dump()["construct"] == "goal"


def test_duplicate_entry_ids_rejected():
    with pytest.raises(ValidationError, match="unique"):
        c.PersonaImportItem.model_validate({"persona_id": "s", "entries": [entry(), entry()]})


def test_observation_and_length_rules():
    with pytest.raises(ValidationError, match="strictly increasing"):
        c.ObservationBatch.model_validate({"items": [{"seq": 2, "kind": "event", "content": "a"},
                                                     {"seq": 2, "kind": "event", "content": "b"}]})
    with pytest.raises(ValidationError, match="hard_min"):
        c.LengthContract.model_validate({"unit": "words", "soft": [10, 80], "hard": [25, 160]})


def test_episode_ids_unique():
    dup = json.loads(json.dumps(EPISODE))
    dup["participants"][1]["persona_id"] = "student-283"
    with pytest.raises(ValidationError, match="unique"):
        c.EpisodeCreate.model_validate(dup)


def test_edit_uses_from_to_on_the_wire():
    wire = {"entry_id": "x", "field": "value.baseline", "from": 0.33, "to": 0.43}
    assert c.Edit.model_validate(wire).model_dump() == wire


async def test_fake_model_makes_exactly_two_calls_per_action():
    model = FakePersonaModel()
    state = await model.infer_state(StateInput(
        beliefs=[entry()], previous_state=None, situation=EPISODE["situation"],
        self_participant_id="p1", participants=EPISODE["participants"],
        position=EPISODE["participants"][0]["position"],
        observations=[{"seq": 12, "content": "……"}], memories=[], task=PREPARE["task"]))
    out = await model.generate_and_review(GenerationInput(
        state=state.value, beliefs=[entry()], situation=EPISODE["situation"], observations=[],
        self_participant_id="p1", participants=EPISODE["participants"],
        task=PREPARE["task"], output_contract=PREPARE["output_contract"]))
    assert model.calls == ["infer_state", "generate_and_review"]
    assert state.value.L3["identity_position"] == "组长"
    assert len(out.value.final.text.split()) == 55
