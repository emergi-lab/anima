"""The owner's personas: list for switching, at most five, delete with everything they lived."""

import sqlite3

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
