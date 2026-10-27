"""S3: after-episode memory + monitoring jobs, recall in later episodes, re-runs, outbox."""

import time

from fastapi.testclient import TestClient
from sqlalchemy import select

from persona_os.domain.ports import (
    Consolidation,
    EpisodicMemory,
    ModelResult,
    MonitoringItem,
    RelationalMemory,
)
from persona_os.infra.db import MonitoringRow
from persona_os.infra.fake_model import FakePersonaModel
from persona_os.infra.openai_model import ModelCallError

AUTH = {"Authorization": "Bearer t"}
INSTRUMENT = {"instrument": "test"}


def persona(pid: str) -> dict:
    return {"persona_id": pid, "entries": [
        {"entry_id": "L1_extraversion", "layer": "L1", "construct": "trait",
         "construct_id": "trait.extraversion", "value": {"baseline": 0.2}, "text": "我比较安静。",
         "source": "measurement", "source_ref": INSTRUMENT}]}


def episode(eid: str) -> dict:
    return {"episode_id": eid, "situation": {"title": eid, "description": "talk", "language": "en"},
            "participants": [
                {"participant_id": "p-a", "display_name": "Ann", "persona_id": "p-a"},
                {"participant_id": "p-b", "display_name": "Bo", "persona_id": "p-b"},
                {"participant_id": "t", "display_name": "Teacher"}]}


def prepare(action_id: str, who: str, upto: int) -> dict:
    return {"action_id": action_id, "participant_id": who, "upto_seq": upto,
            "task": {"instruction": "say something"},
            "output_contract": {"length": {"unit": "words", "soft": [5, 8], "hard": [3, 20]}}}


class Recording(FakePersonaModel):
    """Fake that remembers the memories each action was given and can fail consolidation."""

    def __init__(self, fail: int = 0, output: Consolidation | None = None):
        super().__init__()
        self.fail, self.output, self.recalled = fail, output, []

    async def infer_state(self, data):
        self.recalled.append(data.memories)
        return await super().infer_state(data)

    async def respond(self, data, **kw):
        self.recalled.append(data.memories)
        return await super().respond(data, **kw)

    async def consolidate(self, data):
        if self.fail:
            self.fail -= 1
            raise ModelCallError("http_400", "bad request", False)
        if self.output is not None:
            self.calls.append("consolidate")
            return ModelResult(self.output, "scripted", latency_ms=1)
        return await super().consolidate(data)


def run_episode(client: TestClient, eid: str) -> None:
    """Open an episode, let both personas speak once, close it."""
    assert client.post("/v1/episodes", headers=AUTH, json=episode(eid)).status_code == 200
    client.post(f"/v1/episodes/{eid}/observations", headers=AUTH, json={"items": [
        {"seq": 0, "kind": "event", "content": "start"}]})
    for seq, (action, who) in enumerate([(f"{eid}-1", "p-a"), (f"{eid}-2", "p-b")], start=1):
        assert client.post(f"/v1/episodes/{eid}/actions:prepare", headers=AUTH,
                           json=prepare(action, who, seq - 1)).status_code == 200
        assert client.post(f"/v1/episodes/{eid}/actions/{action}:commit", headers=AUTH,
                           json={"seq": seq}).status_code == 200
    assert client.post(f"/v1/episodes/{eid}:close", headers=AUTH,
                       json={"outcome": {"text": "agreed"}}).status_code == 200


def settled(client: TestClient, eid: str) -> list[dict]:
    for _ in range(200):
        jobs = client.get(f"/v1/episodes/{eid}/jobs", headers=AUTH).json()
        if jobs and all(j["status"] in ("done", "failed") for j in jobs):
            return jobs
        time.sleep(0.02)
    raise AssertionError(f"jobs did not settle: {jobs}")


def test_close_writes_memories_and_monitoring_then_later_episodes_recall_them(make):
    model = Recording()
    client = make(model)
    run_episode(client, "e1")
    jobs = settled(client, "e1")
    assert [(j["persona_id"], j["job"], j["status"]) for j in jobs] == [
        ("p-a", "consolidate", "done"), ("p-b", "consolidate", "done")]
    assert model.calls.count("consolidate") == 2  # one call per persona

    mems = client.get("/v1/personas/p-a/memories", headers=AUTH).json()
    kinds = {(m["kind"], m["about_name"]) for m in mems}
    assert kinds == {("episodic", None), ("relational", "Bo")}  # no relation with non-personas
    assert all(m["episode_id"] == "e1" for m in mems)

    # The next episode's call 1 receives them and may cite them as evidence.
    model.recalled.clear()
    run_episode(client, "e2")
    first = model.recalled[0]
    assert [m["kind"] for m in first] == ["episodic", "relational"]
    assert first[1]["about"] == "Bo" and all(m["id"].startswith("mem:") for m in first)
    # Memories of the episode being played are never recalled into it.
    settled(client, "e2")
    model.recalled.clear()
    run_episode(client, "e3")
    assert len([m for m in model.recalled[0] if m["kind"] == "episodic"]) == 2


def test_invalid_references_are_dropped(make):
    bad = Consolidation(
        episodic=EpisodicMemory(summary="we talked", evidence=["obs:1", "obs:99", "mem:5"]),
        relational=[RelationalMemory(participant_id="p-b", summary="Bo agreed", evidence=[]),
                    RelationalMemory(participant_id="ghost", summary="?", evidence=[])],
        monitoring=[
            MonitoringItem(entry_id="L1_extraversion", direction="above", note="loud",
                           evidence=["obs:1", "obs:2"]),  # obs:2 is Bo's line → dropped
            MonitoringItem(entry_id="L9_nope", direction="below", note="?", evidence=["obs:1"]),
            MonitoringItem(entry_id="L1_extraversion", direction="below", note="other's words",
                           evidence=["obs:2"]),  # only someone else's words → not a deviation
        ])
    client = make(Recording(output=bad))
    run_episode(client, "e1")
    settled(client, "e1")
    mems = client.get("/v1/personas/p-a/memories", headers=AUTH).json()
    episodic = next(m for m in mems if m["kind"] == "episodic")
    assert episodic["evidence"] == ["obs:1"]
    assert [m["about_name"] for m in mems if m["kind"] == "relational"] == ["Bo"]

    async def monitoring():
        async with client.app.state.aftercare.sessions() as s:
            return (await s.scalars(select(MonitoringRow).where(
                MonitoringRow.persona_id == "p-a"))).all()
    rows = client.portal.call(monitoring)
    assert [(r.entry_id, r.direction, r.evidence) for r in rows] == [
        ("L1_extraversion", "above", ["obs:1"])]


def test_failed_jobs_are_reported_and_can_be_rerun(make):
    client = make(Recording(fail=2))
    run_episode(client, "e1")
    jobs = settled(client, "e1")
    assert {j["status"] for j in jobs} == {"failed"} and "http_400" in jobs[0]["error"]
    events = client.get("/v1/events?after=0", headers=AUTH).json()
    assert [e["type"] for e in events["items"]] == ["episode.jobs_failed"] * 2
    assert client.get(f"/v1/events?after={events['next_after']}",
                      headers=AUTH).json() == {"items": [], "next_after": events["next_after"]}

    jobs = client.post("/v1/episodes/e1/jobs:run", headers=AUTH)
    assert jobs.status_code == 200
    jobs = settled(client, "e1")
    assert {(j["status"], j["runs"]) for j in jobs} == {("done", 2)}
    # Re-running a finished episode replaces, never duplicates, its memories.
    client.post("/v1/episodes/e1/jobs:run", headers=AUTH)
    settled(client, "e1")
    assert len(client.get("/v1/personas/p-a/memories", headers=AUTH).json()) == 2


def test_rerun_rules(make):
    client = make(Recording())
    assert client.post("/v1/episodes", headers=AUTH, json=episode("e1")).status_code == 200
    assert client.post("/v1/episodes/e1/jobs:run", headers=AUTH).json()["error"]["code"] == \
        "episode_open"
    assert client.get("/v1/episodes/nope/jobs", headers=AUTH).status_code == 404
    assert client.get("/v1/personas/nope/memories", headers=AUTH).status_code == 404
