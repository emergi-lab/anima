"""S4: reflection after the owner completes feedback, constraint review, frozen-copy tests,
owner decision and commit (immediately, or before the persona's next episode)."""

from persona_os.domain.ports import JudgeOutput, ModelResult, Verdict
from tests.test_aftercare import AUTH, Recording, episode, run_episode, settled
from tests.test_feedback import TAXONOMY


class Quiet(Recording):
    """Self-check never finds a deviation: only the owner's judgments are evidence."""

    async def consolidate(self, data):
        result = await super().consolidate(data)
        return ModelResult(result.value.model_copy(update={"monitoring": []}), "quiet",
                           latency_ms=1)


def rate(client, eid, action, persona, code):
    r = client.post("/v1/feedback", headers=AUTH, json={
        "feedback_id": f"fb-{action}", "persona_id": persona, "action_id": action,
        "taxonomy": "anima-v1", "judgment": "reject", "reason_code": code,
        "note": "换我会说：这不该成为证据"})
    assert r.status_code == 200, r.text


def judged_episode(client, eid, code_a="too_harsh", code_b="wording_only"):
    """Run an episode, rate both personas' line, complete, wait for all jobs."""
    run_episode(client, eid)
    settled(client, eid)  # students rate after the class; memories are written by then
    rate(client, eid, f"{eid}-1", "p-a", code_a)
    rate(client, eid, f"{eid}-2", "p-b", code_b)
    for persona in ("p-a", "p-b"):
        assert client.post(f"/v1/episodes/{eid}/feedback:complete", headers=AUTH,
                           json={"persona_id": persona}).status_code == 200
    return settled(client, eid)


def candidates(client, persona="p-a", status=None):
    query = f"?status={status}" if status else ""
    return client.get(f"/v1/personas/{persona}/candidates{query}", headers=AUTH).json()


def test_three_matching_judgments_make_a_tested_candidate_that_the_owner_commits(make):
    model = Quiet()
    client = make(model)
    client.put("/v1/feedback-taxonomies/anima-v1", headers=AUTH, json=TAXONOMY)
    for eid in ("e1", "e2"):
        jobs = judged_episode(client, eid)
        assert {(j["job"], j["status"]) for j in jobs} == {("consolidate", "done"),
                                                           ("reflect", "done")}
    assert candidates(client) == [] and "reflect" not in model.calls  # < 3: no model call

    judged_episode(client, "e3")
    assert model.calls.count("reflect") == 1  # p-b only has wording feedback: no evidence
    assert model.calls.count("judge_tests") == 1
    assert model.calls.count("generate_and_review") == 6 + 12  # 3 episodes × 2, 6 tests × 2
    [cand] = candidates(client, status="awaiting_owner")
    # The fake asked for -0.30; the term cap for L1 is 0.10.
    assert cand["edits"] == [{"entry_id": "L1_extraversion", "field": "value.baseline",
                              "from": 0.2, "to": 0.1}]
    assert cand["strength"] == "strong" and len(cand["evidence_for"]) == 3
    assert all(e["ref"].startswith("feedback:") and "证据" not in (e["quote"] or "")
               for e in cand["evidence_for"])  # the owner's note is never evidence
    assert [t["type"] for t in cand["test"]].count("out_of_scope") == 2
    assert candidates(client, "p-b") == []
    types = [e["type"] for e in client.get("/v1/events", headers=AUTH).json()["items"]]
    assert types.count("candidate.awaiting_owner") == 1 and types.count(
        "reflection.completed") == 6

    decided = client.post(f"/v1/candidates/{cand['candidate_id']}:decide", headers=AUTH,
                          json={"decision": "approve"})
    assert decided.json()["status"] == "committed"  # no open episode: committed now
    summary = client.get("/v1/personas/p-a", headers=AUTH).json()
    assert summary["k"] == 1
    version = client.get(f"/v1/personas/p-a/versions/{summary['active_version_id']}",
                         headers=AUTH).json()
    entry = next(e for e in version["entries"] if e["entry_id"] == "L1_extraversion")
    assert version["origin"] == "evolution" and entry["value"]["baseline"] == 0.1
    assert f"candidate:{cand['candidate_id']}" in entry["evidence_refs"]
    assert client.post(f"/v1/candidates/{cand['candidate_id']}:decide", headers=AUTH,
                       json={"decision": "approve"}).json()["status"] == "committed"
    assert client.post(f"/v1/candidates/{cand['candidate_id']}:decide", headers=AUTH,
                       json={"decision": "reject"}).status_code == 409

    # Used evidence is not reused; three new judgments, but the term cap is spent.
    for eid in ("e4", "e5", "e6"):
        judged_episode(client, eid)
    assert len(candidates(client)) == 1


def test_approval_during_an_episode_commits_before_the_next_one(make):
    client = make(Quiet())
    client.put("/v1/feedback-taxonomies/anima-v1", headers=AUTH, json=TAXONOMY)
    for eid in ("e1", "e2", "e3"):
        judged_episode(client, eid)
    [cand] = candidates(client, status="awaiting_owner")
    assert client.post("/v1/episodes", headers=AUTH, json=episode("live")).status_code == 200
    assert client.post(f"/v1/candidates/{cand['candidate_id']}:decide", headers=AUTH,
                       json={"decision": "approve"}).json()["status"] == "approved"
    assert client.get("/v1/personas/p-a", headers=AUTH).json()["k"] == 0
    client.post("/v1/episodes/live:close", headers=AUTH, json={})
    assert client.post("/v1/episodes", headers=AUTH, json=episode("next")).status_code == 200
    assert client.get("/v1/personas/p-a", headers=AUTH).json()["k"] == 1
    assert candidates(client)[0]["status"] == "committed"


def test_a_change_that_leaks_out_of_scope_never_reaches_the_owner(make):
    class Leaky(Quiet):
        async def judge_tests(self, data):
            self.calls.append("judge_tests")
            return ModelResult(JudgeOutput(verdicts=[
                Verdict(index=x["index"], verdict="realized" if x["type"] == "target"
                        else "changed") for x in data.cases]), "leaky", latency_ms=1)

    client = make(Leaky())
    client.put("/v1/feedback-taxonomies/anima-v1", headers=AUTH, json=TAXONOMY)
    for eid in ("e1", "e2", "e3"):
        judged_episode(client, eid)
    assert candidates(client, status="awaiting_owner") == []
    assert [x["status"] for x in candidates(client)] == ["no_commit"]
    assert client.post("/v1/candidates/nope:decide", headers=AUTH,
                       json={"decision": "approve"}).status_code == 404


def test_the_same_deviation_in_two_episodes_is_evidence_too(make):
    """The fake self-check flags the same entry every episode; the fake reflection defers it."""
    model = Recording()
    client = make(model)
    client.put("/v1/feedback-taxonomies/anima-v1", headers=AUTH, json=TAXONOMY)
    judged_episode(client, "e1", code_a="wording_only")
    assert "reflect" not in model.calls  # one episode is not yet a pattern
    judged_episode(client, "e2", code_a="wording_only")
    assert model.calls.count("reflect") == 2  # both personas now show a pattern
    assert candidates(client) == []
