"""S2 execution engine: two calls per action, prepare → commit/discard, checks, idempotency."""

import json

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from persona_os.api.app import create_app
from persona_os.config import Settings
from persona_os.domain.contract import ActionOutput
from persona_os.domain.ports import GenerationDraft, ModelResult
from persona_os.infra.fake_model import FakePersonaModel
from persona_os.infra.openai_model import ModelCallError

AUTH = {"Authorization": "Bearer t"}
INSTRUMENT = {"instrument": "test"}
PERSONA = {"persona_id": "p-a", "entries": [
    {"entry_id": "L1_extraversion", "layer": "L1", "construct": "trait",
     "construct_id": "trait.extraversion", "value": {"baseline": 0.2}, "text": "我比较安静。",
     "source": "measurement", "source_ref": INSTRUMENT},
    {"entry_id": "L2_value_benevolence", "layer": "L2", "construct": "value",
     "construct_id": "value.benevolence", "value": {"baseline": 0.8}, "text": "我在意身边的人。",
     "source": "measurement", "source_ref": INSTRUMENT}]}
EPISODE = {"episode_id": "e1",
           "situation": {"title": "w3", "description": "讨论", "language": "en"},
           "participants": [
               {"participant_id": "p1", "display_name": "A", "persona_id": "p-a",
                "position": {"identity": "leader", "responsibilities": ["open the discussion"]}},
               {"participant_id": "p2", "display_name": "B"}]}


def prepare_body(action_id="a1", upto=1, **contract):
    return {"action_id": action_id, "participant_id": "p1", "upto_seq": upto,
            "task": {"instruction": "say something"},
            "output_contract": {"length": {"unit": "words", "soft": [5, 8], "hard": [3, 20]},
                                **contract}}


class Scripted(FakePersonaModel):
    """Fake whose call 2 returns queued drafts or raises queued errors."""

    def __init__(self, outputs):
        super().__init__()
        self.outputs = list(outputs)

    async def generate_and_review(self, data):
        self.calls.append("generate_and_review")
        self.feedback = data.repair_feedback
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        draft = GenerationDraft(draft=out, review=[], final=ActionOutput(text=out),
                                action_basis={"L2": "cares", "entries": ["L2_value_benevolence",
                                                                         "nope"]})
        return ModelResult(draft, "scripted", latency_ms=3)


def make_client(tmp_path, model=None):
    settings = Settings(_env_file=None, service_tokens=SecretStr("t=anima"),
                        database_url="sqlite+aiosqlite://",
                        model_backend="fake", openai_api_key=SecretStr(""),
                        model_file=str(tmp_path / "model.json"))
    return TestClient(create_app(settings, model=model or FakePersonaModel()))


@pytest.fixture
def setup(tmp_path):
    def _setup(model=None):
        client = make_client(tmp_path, model)
        client.__enter__()
        assert client.post("/v1/personas:import", headers=AUTH,
                           json={"personas": [PERSONA]}).status_code == 200
        assert client.post("/v1/episodes", headers=AUTH, json=EPISODE).status_code == 200
        client.post("/v1/episodes/e1/observations", headers=AUTH, json={"items": [
            {"seq": 0, "kind": "event", "content": "start"},
            {"seq": 1, "kind": "utterance", "actor": "p2", "content": "hi",
             "addressed_to": ["p1"]}]})
        return client
    clients = []
    yield lambda model=None: clients.append(_setup(model)) or clients[-1]
    for cl in clients:
        try:
            # Closing an episode schedules work; drain it before disposing the test DB.
            cl.portal.call(cl.app.state.aftercare.idle)
        finally:
            cl.__exit__(None, None, None)


def test_lifecycle_two_calls_commit_and_state_chain(setup):
    model = FakePersonaModel()
    client = setup(model)
    r = client.post("/v1/episodes/e1/actions:prepare", headers=AUTH, json=prepare_body())
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["status"] == "prepared" and first["usage"]["model_calls"] == 2
    assert model.calls == ["infer_state", "generate_and_review"]

    # idempotent: same request returns the stored result without new model calls
    assert client.post("/v1/episodes/e1/actions:prepare", headers=AUTH,
                       json=prepare_body()).json() == first
    assert len(model.calls) == 2
    conflict = client.post("/v1/episodes/e1/actions:prepare", headers=AUTH,
                           json=prepare_body(upto=0))
    assert conflict.status_code == 409

    done = client.post("/v1/episodes/e1/actions/a1:commit", headers=AUTH, json={"seq": 2})
    assert done.json()["status"] == "committed"
    assert client.post("/v1/episodes/e1/actions/a1:commit", headers=AUTH,
                       json={"seq": 2}).status_code == 200
    assert client.post("/v1/episodes/e1/actions/a1:discard", headers=AUTH).status_code == 409

    # the committed state becomes Z_{t-1}; the committed text is now an observation
    seen = {}

    async def spy(data):
        seen["prev"] = data.previous_state
        seen["obs"] = [o["seq"] for o in data.observations]
        return await FakePersonaModel.infer_state(model, data)

    model.infer_state = spy
    client.post("/v1/episodes/e1/actions:prepare", headers=AUTH,
                json=prepare_body("a2", upto=2))
    assert seen["prev"] is not None and seen["prev"].L3["identity_position"] == "leader"
    assert seen["obs"] == [0, 1, 2]


def test_discarded_state_never_feeds_the_next_action(setup):
    model = FakePersonaModel()
    client = setup(model)
    client.post("/v1/episodes/e1/actions:prepare", headers=AUTH, json=prepare_body())
    assert client.post("/v1/episodes/e1/actions/a1:discard",
                       headers=AUTH).json()["status"] == "discarded"
    seen = {}

    async def spy(data):
        seen["prev"] = data.previous_state
        return await FakePersonaModel.infer_state(model, data)

    model.infer_state = spy
    client.post("/v1/episodes/e1/actions:prepare", headers=AUTH, json=prepare_body("a2"))
    assert seen["prev"] is None


def test_failed_checks_redo_call_two_once_with_feedback(setup):
    model = Scripted(["too short", "one two three four five six"])
    client = setup(model)
    r = client.post("/v1/episodes/e1/actions:prepare", headers=AUTH, json=prepare_body())
    assert r.status_code == 200, r.text
    assert r.json()["usage"] == r.json()["usage"] | {"model_calls": 2, "attempts": 3}
    assert "allowed 3-20" in model.feedback
    explanation = client.get("/v1/actions/a1/explanation", headers=AUTH).json()
    assert [e["entry_id"] for e in explanation["entries"]] == ["L2_value_benevolence"]


def test_generation_failure_is_502_and_not_faked(setup):
    model = Scripted(["no", "no"])
    client = setup(model)
    r = client.post("/v1/episodes/e1/actions:prepare", headers=AUTH, json=prepare_body())
    assert r.status_code == 502 and r.json()["error"]["code"] == "generation_failed"
    assert client.post("/v1/episodes/e1/actions/a1:commit", headers=AUTH,
                       json={"seq": 2}).status_code == 404


def test_unavailable_model_is_503(setup):
    model = Scripted([ModelCallError("http_503", "down", True)] * 2)
    client = setup(model)
    r = client.post("/v1/episodes/e1/actions:prepare", headers=AUTH, json=prepare_body())
    assert r.status_code == 503 and r.json()["error"]["retryable"] is True


def test_extra_schema_is_enforced(setup):
    client = setup()
    # A number: the fake model fills only enum and string fields, so this stays missing.
    schema = {"type": "object", "required": ["speech_act"],
              "properties": {"speech_act": {"type": "integer"}}}
    r = client.post("/v1/episodes/e1/actions:prepare", headers=AUTH,
                    json=prepare_body(extra_schema=schema))
    assert r.status_code == 502 and "speech_act" in r.json()["error"]["message"]


def test_episode_rules(setup):
    client = setup()
    assert client.post("/v1/episodes", headers=AUTH, json=EPISODE).status_code == 200  # same
    busy = dict(EPISODE, episode_id="e2")
    assert client.post("/v1/episodes", headers=AUTH, json=busy).json()["error"]["code"] == \
        "persona_busy"
    stale = client.post("/v1/episodes/e1/observations", headers=AUTH,
                        json={"items": [{"seq": 1, "kind": "event", "content": "changed"}]})
    assert stale.json()["error"]["code"] == "observation_conflict"
    p2 = client.post("/v1/episodes/e1/actions:prepare", headers=AUTH,
                     json=prepare_body() | {"participant_id": "p2"})
    assert p2.json()["error"]["code"] == "not_a_persona"
    client.post("/v1/episodes/e1/actions:prepare", headers=AUTH, json=prepare_body())
    closed = client.post("/v1/episodes/e1:close", headers=AUTH, json={}).json()
    assert closed["status"] == "closed"
    assert client.post("/v1/episodes/e1/actions/a1:commit", headers=AUTH,
                       json={"seq": 2}).json()["error"]["code"] == "action_discarded"
    assert client.post("/v1/episodes", headers=AUTH, json=busy).status_code == 200


def test_output_language_follows_the_situation(setup):
    model = Scripted(["我觉得应该允许使用，但是要有规则。",
                      "I think it should be allowed with rules."])
    client = setup(model)
    r = client.post("/v1/episodes/e1/actions:prepare", headers=AUTH,
                    json=prepare_body() | {"output_contract": {
                        "length": {"unit": "chars", "soft": [5, 40], "hard": [3, 60]}}})
    assert r.status_code == 200 and r.json()["usage"]["attempts"] == 3
    assert "written in en" in model.feedback


class ChineseBasis(Scripted):
    """English line, Chinese self-explanation (as zh-survey students' personas produce)."""

    async def generate_and_review(self, data):
        result = await super().generate_and_review(data)
        result.value.action_basis = {"L3": "我是组长，要先把顺序讲清楚", "L2": "我在意公平",
                                     "L1": "", "entries": []}
        return result


def test_only_the_spoken_line_must_follow_the_situation_language(setup):
    model = ChineseBasis(["I think it should be allowed with clear rules."])
    client = setup(model)
    r = client.post("/v1/episodes/e1/actions:prepare", headers=AUTH,
                    json=prepare_body() | {"output_contract": {
                        "length": {"unit": "chars", "soft": [5, 60], "hard": [3, 80]}}})
    assert r.status_code == 200, r.text
    assert r.json()["usage"]["attempts"] == 2  # state inference + one generation, no redo


def test_final_object_is_found_while_streaming():
    from persona_os.infra.openai_model import _FINAL, _closed_object

    full = json.dumps({"now": "说 \"final\" 一词", "final": {
        "text": 'a}"b\\', "addressed_to": [], "extra": {"emotion": "calm"}}, "state": {}},
        ensure_ascii=False)
    found = _closed_object(full, _FINAL)
    assert json.loads(found)["text"] == 'a}"b\\'
    assert _closed_object(full[:full.index('"state"') - 3], _FINAL) is None


def test_state_fields_written_beside_state_are_moved_in():
    from persona_os.domain.ports import Response

    r = Response.model_validate({"final": {"text": "嗯"}, "state": {"L1": {}, "L2": {}, "L3": {}},
                                 "dropped": ["x"], "unknown": ["y"], "action_basis": {}})
    assert r.state.dropped == ["x"] and r.state.unknown == ["y"]
