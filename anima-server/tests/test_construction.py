"""S1 construction: B0 import, unknown placeholders, idempotency, replace, isolation, identity."""

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from persona_os.api.app import create_app
from persona_os.config import Settings

AUTH = {"Authorization": "Bearer t-anima"}
OTHER = {"Authorization": "Bearer t-other"}
INSTRUMENT = {"instrument": "Digital Twin Personality Assessment v1 / dt-1.1-fourpoint"}


@pytest.fixture
def client():
    settings = Settings(service_tokens=SecretStr("t-anima=anima,t-other=other"),
                        database_url="sqlite+aiosqlite://")
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def measured(entry_id, layer, construct, construct_id, value, text="……"):
    return {"entry_id": entry_id, "layer": layer, "construct": construct,
            "construct_id": construct_id, "value": value, "text": text,
            "source": "measurement", "source_ref": INSTRUMENT,
            "evidence_refs": ["item:extraversion-01"]}


SURVEYED = {"persona_id": "cb-surveyed", "entries": [
    measured("L3_imago_1", "L3", "imago", "imago.sage", {"baseline": 0.9, "rank": 1}),
    measured("L2_need_autonomy", "L2", "need", "need.autonomy",
             {"satisfaction": 0.167, "frustration": 0.667, "scale": "0-1"}),
    measured("L2_value_benevolence", "L2", "value", "value.benevolence", {"baseline": 0.0}),
    measured("L2_strategy_planning", "L2", "strategy", "strategy.planning", {"baseline": 0.333}),
    measured("L1_extraversion_sociability", "L1", "trait", "trait.extraversion.sociability",
             {"baseline": 0.167}),
], "material": [{"ref": "survey:item:extraversion-01",
                 "text": "我性格外向，善于社交。——很不符合我"}]}

MINIMAL = {"persona_id": "cb-minimal", "entries": [{
    "entry_id": "L3_identity_course", "layer": "L3", "construct": "overall_identity",
    "construct_id": "overall_identity.course_member", "text": "我是 CB2500 课程 T01 教学班的学生。",
    "source": "configured", "value": None}]}


def import_(client, *personas, mode="create_only", headers=AUTH):
    return client.post("/v1/personas:import", headers=headers,
                       json={"mode": mode, "personas": list(personas)})


def test_import_creates_v0_and_marks_missing_categories_unknown(client):
    r = import_(client, SURVEYED, MINIMAL)
    assert r.status_code == 200, r.text
    by_id = {x["persona_id"]: x for x in r.json()["results"]}
    assert by_id["cb-surveyed"]["status"] == "created"
    assert sorted(by_id["cb-surveyed"]["unknown_constructs"]) == [
        "goal.unspecified", "life_course.unspecified", "overall_identity.unspecified"]
    # minimal persona: everything except the configured identity is unknown
    assert len(by_id["cb-minimal"]["unknown_constructs"]) == 7

    summary = client.get("/v1/personas/cb-surveyed", headers=AUTH).json()
    assert summary["k"] == 0 and summary["entry_count"] == 8
    assert summary["active_version_id"] == by_id["cb-surveyed"]["version_id"]

    versions = client.get("/v1/personas/cb-surveyed/versions", headers=AUTH).json()
    assert [v["k"] for v in versions] == [0] and versions[0]["origin"] == "construction"
    entries = {e["entry_id"]: e for e in versions[0]["entries"]}
    assert entries["L2_need_autonomy"]["value"]["frustration"] == 0.667
    assert entries["L2_goal_unknown"]["construct"] == "goal"
    assert entries["L2_goal_unknown"]["value"] is None


def test_create_only_is_idempotent_and_replace_initial_swaps_v0(client):
    first = import_(client, SURVEYED).json()["results"][0]["version_id"]
    again = import_(client, SURVEYED).json()["results"][0]
    assert again == again | {"status": "skipped", "version_id": first}

    changed = dict(SURVEYED, entries=SURVEYED["entries"][:1])
    replaced = import_(client, changed, mode="replace_initial").json()["results"][0]
    assert replaced["status"] == "replaced" and replaced["version_id"] != first
    versions = client.get("/v1/personas/cb-surveyed/versions", headers=AUTH).json()
    assert len(versions) == 1 and versions[0]["version_id"] == replaced["version_id"]
    assert client.get(f"/v1/personas/cb-surveyed/versions/{first}",
                      headers=AUTH).status_code == 404


def test_namespaces_are_isolated(client):
    import_(client, SURVEYED)
    assert client.get("/v1/personas/cb-surveyed", headers=OTHER).status_code == 404
    assert import_(client, SURVEYED, headers=OTHER).json()["results"][0]["status"] == "created"


@pytest.mark.parametrize("text", ["联系我 someone@my.cityu.edu.hk", "学号 61207842"])
def test_identity_data_is_rejected(client, text):
    bad = dict(MINIMAL, material=[{"ref": "note", "text": text}])
    r = import_(client, bad)
    assert r.status_code == 422 and r.json()["error"]["code"] == "identity_data"
    assert client.get("/v1/personas/cb-minimal", headers=AUTH).status_code == 404


def test_unknown_persona_and_version_404(client):
    assert client.get("/v1/personas/nope", headers=AUTH).json()["error"]["code"] == \
        "persona_not_found"
    import_(client, MINIMAL)
    r = client.get("/v1/personas/cb-minimal/versions/nope", headers=AUTH)
    assert r.status_code == 404 and r.json()["error"]["code"] == "version_not_found"
