"""Self-reflection: diary, checked findings, versions A/B/C within the limits, owner approval."""

from tests.test_aftercare import AUTH, Recording
from tests.test_evolve import chat, persona_from_script


def reflect(client) -> dict:
    r = client.post("/v1/personas/xiaoxi/reflections", headers=AUTH, json={"persona_name": "小希"})
    assert r.status_code == 200, r.text
    client.portal.call(client.app.state.reflections.idle)
    return client.get(f"/v1/reflections/{r.json()['reflection_id']}", headers=AUTH).json()


def test_reflect_propose_versions_and_approve_one(make):
    model = Recording()
    client = make(model)
    b0 = persona_from_script(client)
    r = client.post("/v1/personas/xiaoxi/reflections", headers=AUTH, json={"persona_name": "小希"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "nothing_to_reflect"

    chat(client, "你今天太凶了", "这不像你")
    preview = client.get("/v1/personas/xiaoxi/reflection:preview", headers=AUTH).json()
    assert preview | {"sessions": 1, "owner_lines": 2, "enough": True, "open": None} == preview

    done = reflect(client)
    assert done["status"] == "ready" and done["diary"]
    # two findings cite owner lines; the one resting on its own line alone was dropped
    assert [f["id"] for f in done["findings"]] == ["F1", "F2"]
    assert all(ref.startswith("L") for f in done["findings"] for ref in f["refs"])
    assert [(v["key"], v["title"]) for v in done["versions"]] == [
        ("A", "微调"), ("B", "适度"), ("C", "明显")]
    revise = done["versions"][1]["changes"][0]
    assert revise["before"]["value"]["baseline"] == 0.8
    assert revise["after"]["value"]["baseline"] == 0.6  # 0.3 asked, held to 0.8 − 0.2
    assert revise["evidence"][0] == {"id": "L1", "who": "主人", "text": "你今天太凶了"}
    assert [ch["after"]["construct_id"] for ch in done["versions"][2]["changes"]] == [
        revise["after"]["construct_id"], "value.stimulation"]  # only C adds an entry
    assert client.post("/v1/personas/xiaoxi/reflections", headers=AUTH, json={
        "persona_name": "小希"}).json()["error"]["code"] == "reflection_open"

    tried = client.post(f"/v1/reflections/{done['reflection_id']}/versions/B:try",
                        headers=AUTH).json()
    assert tried["before"] and tried["after"]

    rid = done["reflection_id"]
    assert client.post(f"/v1/reflections/{rid}:approve", headers=AUTH,
                       json={"version": "Z"}).status_code == 422
    approved = client.post(f"/v1/reflections/{rid}:approve", headers=AUTH,
                           json={"version": "B"}).json()
    assert approved["status"] == "approved" and approved["chosen"] == "B"
    history = client.get("/v1/personas/xiaoxi/history", headers=AUTH).json()
    assert history[0]["k"] == 1 and history[0]["version_id"] == approved["committed_version_id"]
    assert history[0]["changes"][0]["after"]["value"]["baseline"] == 0.6
    refs = ",".join(history[0]["changes"][0]["after"]["evidence_refs"])
    assert "主人：你今天太凶了" in client.get(f"/v1/personas/xiaoxi/materials?refs={refs}",
                                          headers=AUTH).json().values()
    assert client.get("/v1/personas/xiaoxi/chats", headers=AUTH).json()[0]["status"] == "closed"
    assert client.post(f"/v1/reflections/{rid}:decline", headers=AUTH).status_code == 409
    assert b0["version_id"] != approved["committed_version_id"]


def test_decline_keeps_the_diary_and_delete_removes_it(make):
    client = make(Recording())
    persona_from_script(client)
    chat(client, "你最近挺乖的")
    done = reflect(client)
    declined = client.post(f"/v1/reflections/{done['reflection_id']}:decline",
                           headers=AUTH).json()
    assert declined["status"] == "declined" and declined["diary"]
    assert len(client.get("/v1/personas/xiaoxi/history", headers=AUTH).json()) == 1  # no change
    listed = client.get("/v1/personas/xiaoxi/reflections", headers=AUTH).json()
    assert [x["status"] for x in listed] == ["declined"]
    assert client.delete("/v1/personas/xiaoxi", headers=AUTH).status_code == 200
    assert client.get(f"/v1/reflections/{done['reflection_id']}",
                      headers=AUTH).status_code == 404
