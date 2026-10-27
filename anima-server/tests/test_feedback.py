"""Owner feedback: taxonomy, one judgment per committed action, changes until completion."""

from tests.test_aftercare import AUTH, Recording, run_episode

TAXONOMY = {"judgments": ["endorse", "partial", "reject"], "reasons": [
    {"code": "too_harsh", "label": "太冲了，我没这么硬", "target": "L1"},
    {"code": "wording_only", "label": "意思对，但我不会这么讲", "target": "wording"}]}


def fb(fid="f1", action="e1-1", persona="p-a", **extra):
    return {"feedback_id": fid, "persona_id": persona, "action_id": action,
            "taxonomy": "anima-v1", "judgment": "reject", "reason_code": "too_harsh"} | extra


def test_feedback_flow(make):
    client = make(Recording())
    run_episode(client, "e1")
    assert client.post("/v1/feedback", headers=AUTH, json=fb()).json()["error"]["code"] == \
        "taxonomy_not_found"
    assert client.put("/v1/feedback-taxonomies/anima-v1", headers=AUTH,
                      json=TAXONOMY).status_code == 200

    def post(body):
        return client.post("/v1/feedback", headers=AUTH, json=body)

    assert post(fb(judgment="meh")).status_code == 422
    assert post(fb(reason_code="nope")).status_code == 422
    assert post(fb(action="e1-2")).status_code == 404  # p-b's line, not p-a's
    assert post(fb(note="换我会说……")).status_code == 200
    # Changing one's mind before completion: same id, latest judgment wins.
    assert post(fb(judgment="partial", reason_code="wording_only")).status_code == 200
    assert post(fb(fid="f2")).json()["error"]["code"] == "feedback_conflict"  # one per action

    got = client.get("/v1/episodes/e1/feedback?persona_id=p-a", headers=AUTH).json()
    assert got["completed_at"] is None
    assert [(i["judgment"], i["reason_code"], i["note"]) for i in got["items"]] == [
        ("partial", "wording_only", None)]

    assert client.post("/v1/episodes/e1/feedback:complete", headers=AUTH,
                       json={"persona_id": "p-a"}).json() == {"status": "completed"}
    assert client.post("/v1/episodes/e1/feedback:complete", headers=AUTH,
                       json={"persona_id": "p-a"}).status_code == 200  # idempotent
    assert post(fb()).json()["error"]["code"] == "feedback_closed"
    got = client.get("/v1/episodes/e1/feedback?persona_id=p-a", headers=AUTH).json()
    assert got["completed_at"] is not None
    # Completion is per owner: p-b can still judge its own line.
    assert post(fb(fid="f3", action="e1-2", persona="p-b", judgment="endorse",
                   reason_code=None)).status_code == 200
    assert client.get("/v1/episodes/e1/feedback?persona_id=nobody",
                      headers=AUTH).status_code == 404
