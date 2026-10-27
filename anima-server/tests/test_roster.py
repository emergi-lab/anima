"""The owner's personas: list for switching, at most five, delete with everything they lived."""

import sqlite3

import pytest

from tests.test_aftercare import AUTH, Recording, persona, settled
from tests.test_distill import finished, setup, start


def test_list_limit_and_delete(make, tmp_path):
    model = Recording()
    client = make(model)  # p-a and p-b
    chat = client.app.state.chat
    r = client.post("/v1/personas/p-a/chat:say", headers=AUTH,
                    json={"text": "你好", "persona_name": "阿甲"})
    sid = r.json()["session_id"]
    client.post("/v1/personas/p-a/chat:end", headers=AUTH)
    client.portal.call(chat.settled)
    settled(client, sid)

    roster = client.get("/v1/personas", headers=AUTH).json()
    assert roster["max"] == 5
    assert [(p["persona_id"], p["name"], p["chats"]) for p in roster["items"]] == [
        ("p-a", "p-a", 1), ("p-b", "p-b", 0)]  # last chatted first; no distillation → id

    # a distilled persona is named after its character
    did = setup(client)
    start(client, did)
    finished(client, did)
    for e in client.get(f"/v1/distillations/{did}/entries", headers=AUTH).json():
        client.patch(f"/v1/distillations/{did}/entries/{e['entry']['entry_id']}", headers=AUTH,
                     json={"status": "accepted"})
    assert client.post(f"/v1/distillations/{did}:commit", headers=AUTH).status_code == 200
    names = {p["persona_id"]: p["name"]
             for p in client.get("/v1/personas", headers=AUTH).json()["items"]}
    assert names["xiaoxi"] == "小希"

    # five at most: imports and new distillations are refused before any model call
    assert client.post("/v1/personas:import", headers=AUTH, json={
        "personas": [persona("p-c"), persona("p-d")]}).status_code == 200
    r = client.post("/v1/personas:import", headers=AUTH, json={"personas": [persona("p-e")]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "persona_limit"
    other = setup(client)
    calls = len(model.calls)
    r = client.post(f"/v1/distillations/{other}:start", headers=AUTH, json={
        "target": {"name": "小希"}, "persona_id": "someone-new"})
    assert r.json()["error"]["code"] == "persona_limit" and len(model.calls) == calls
    assert client.post(f"/v1/distillations/{other}:start", headers=AUTH, json={
        "target": {"name": "小希"}, "persona_id": "xiaoxi"}).status_code == 200  # same persona

    # delete p-a: its chats, memories and actions go; the others stay
    assert client.delete("/v1/personas/p-a", headers=AUTH).json() == {"status": "deleted"}
    assert client.delete("/v1/personas/p-a", headers=AUTH).status_code == 404
    assert client.get("/v1/personas/p-a/chats", headers=AUTH).status_code == 404
    db = sqlite3.connect(next(tmp_path.glob("persona-*.db")))
    for table in ("personas", "belief_versions", "actions", "persona_states", "memories",
                  "episode_personas", "episode_jobs"):
        assert db.execute(f"select count(*) from {table} where persona_id = 'p-a'"
                          ).fetchone()[0] == 0, table
    assert db.execute("select count(*) from episodes where episode_id = ?", (sid,)
                      ).fetchone()[0] == 0
    assert db.execute("select count(*) from observations where episode_id = ?", (sid,)
                      ).fetchone()[0] == 0
    assert db.execute("select count(*) from model_calls where action_id like ?",
                      (f"{sid}-%",)).fetchone()[0] == 0
    assert db.execute("select count(*) from personas").fetchone()[0] == 4

    # deleting a distilled persona keeps its distillation, which can be imported again
    finished(client, other)
    assert client.delete("/v1/personas/xiaoxi", headers=AUTH).status_code == 200
    assert client.get(f"/v1/distillations/{did}", headers=AUTH).status_code == 200
    assert client.post(f"/v1/distillations/{did}:commit", headers=AUTH).status_code == 200
    assert "xiaoxi" in [p["persona_id"]
                        for p in client.get("/v1/personas", headers=AUTH).json()["items"]]


@pytest.mark.parametrize("already_deleted", [False, True])
def test_delete_all_data_including_retained_distillations(make, tmp_path, already_deleted):
    client = make(Recording())
    dids = [setup(client), setup(client)]
    for did in dids:
        start(client, did)
        finished(client, did)
        for row in client.get(f"/v1/distillations/{did}/entries", headers=AUTH).json():
            client.patch(f"/v1/distillations/{did}/entries/{row['entry']['entry_id']}",
                         headers=AUTH, json={"status": "accepted"})
        assert client.post(f"/v1/distillations/{did}:commit", headers=AUTH).status_code == 200
    unrelated = setup(client)
    if already_deleted:
        assert client.delete("/v1/personas/xiaoxi", headers=AUTH).status_code == 200
    db = sqlite3.connect(next(tmp_path.glob("persona-*.db")))
    # Same persona ID in another namespace must remain untouched.
    db.execute("INSERT INTO distillations (namespace, distillation_id, title, stage, kind, "
               "persona_id, language, created_at, updated_at) "
               "SELECT 'other', 'foreign-project', title, stage, kind, persona_id, language, "
               "created_at, updated_at FROM distillations WHERE distillation_id = ?", (dids[0],))
    db.commit()
    for table in ("distill_sources", "distill_segments", "distill_evidence", "distill_entries"):
        assert db.execute(f"SELECT count(*) FROM {table} WHERE distillation_id = ?",
                          (dids[0],)).fetchone()[0] > 0
    assert client.delete("/v1/personas/xiaoxi/all-data").status_code == 401
    result = client.delete("/v1/personas/xiaoxi/all-data", headers=AUTH)
    assert result.status_code == 200 and result.json() == {"status": "deleted_all"}
    for did in dids:
        assert client.get(f"/v1/distillations/{did}", headers=AUTH).status_code == 404
        for table in ("distillations", "distill_sources", "distill_segments",
                      "distill_evidence", "distill_entries"):
            assert db.execute(f"SELECT count(*) FROM {table} WHERE distillation_id = ?",
                              (did,)).fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM model_calls WHERE action_id LIKE ?",
                          (f"distill:{did}/%",)).fetchone()[0] == 0
    assert client.get(f"/v1/distillations/{unrelated}", headers=AUTH).status_code == 200
    assert db.execute("SELECT count(*) FROM distillations WHERE namespace = 'other'"
                      ).fetchone()[0] == 1
    assert {p["persona_id"] for p in client.get("/v1/personas", headers=AUTH).json()["items"]
            } == {"p-a", "p-b"}
    assert client.delete("/v1/personas/xiaoxi/all-data", headers=AUTH).status_code == 404
    db.close()


def test_delete_all_data_refuses_running_project_atomically(make, tmp_path):
    client = make(Recording())
    did = setup(client)
    db = sqlite3.connect(next(tmp_path.glob("persona-*.db")))
    # A deterministic running-job marker, without launching a background model job.
    db.execute("UPDATE distillations SET persona_id = 'p-a', stage = 'running' "
               "WHERE distillation_id = ?", (did,))
    db.commit()
    result = client.delete("/v1/personas/p-a/all-data", headers=AUTH)
    assert result.status_code == 409 and result.json()["error"]["code"] == "persona_busy"
    assert client.get("/v1/personas/p-a", headers=AUTH).status_code == 200
    assert db.execute("SELECT count(*) FROM distill_sources WHERE distillation_id = ?",
                      (did,)).fetchone()[0] > 0
    db.close()
