"""A4: evolution as distillation — recent chats revise the active version within small steps."""

from persona_os.distill import evolve as ev
from persona_os.distill.ports import Revision
from tests.test_aftercare import AUTH, Recording
from tests.test_distill import finished, setup, start


def persona_from_script(client) -> dict:
    """xiaoxi's B0 from the distillation test script (one L2 value entry, baseline 0.8)."""
    did = setup(client)
    start(client, did)
    finished(client, did)
    for e in client.get(f"/v1/distillations/{did}/entries", headers=AUTH).json():
        client.patch(f"/v1/distillations/{did}/entries/{e['entry']['entry_id']}", headers=AUTH,
                     json={"status": "accepted"})
    return client.post(f"/v1/distillations/{did}:commit", headers=AUTH).json()


def chat(client, *lines: str) -> None:
    for text in lines:
        r = client.post("/v1/personas/xiaoxi/chat:say", headers=AUTH,
                        json={"text": text, "persona_name": "小希"})
        assert r.status_code == 200, r.text
    client.portal.call(client.app.state.chat.settled)  # replies are stored after they return


def test_evolution_revises_from_owner_lines_and_commits_next_version(make):
    model = Recording()
    client = make(model)
    b0 = persona_from_script(client)
    preview = client.get("/v1/personas/xiaoxi/evolution:preview", headers=AUTH).json()
    assert preview["enough"] is False and preview["sessions"] == 0

    chat(client, "你今天太凶了", "这不像你")
    preview = client.get("/v1/personas/xiaoxi/evolution:preview?persona_name=小希",
                         headers=AUTH).json()
    assert preview | {"since_version": 0, "sessions": 1, "owner_lines": 2, "persona_lines": 2,
                      "deviations": 0, "enough": True, "running": None} == preview

    draft = client.post("/v1/personas/xiaoxi/evolutions", headers=AUTH,
                        json={"persona_name": "小希"}).json()
    did = draft["distillation_id"]
    assert draft["kind"] == "evolution" and draft["base_version_id"] == b0["version_id"]
    assert [s["kind"] for s in draft["sources"]] == ["chat"]
    assert client.post("/v1/personas/xiaoxi/evolutions", headers=AUTH,
                       json={"persona_name": "小希"}).json()["error"]["code"] == "evolution_open"
    assert all(x["kind"] == "initial"
               for x in client.get("/v1/distillations", headers=AUTH).json())

    r = client.post(f"/v1/distillations/{did}:start", headers=AUTH, json={
        "target": {"name": "小希", "aliases": []}, "persona_id": "xiaoxi"})  # as the pet sends it
    assert r.status_code == 200, r.text
    body = finished(client, did)
    assert body["stage"] == "review", body
    assert body["progress"]["evidence"] == 2  # the persona's own line was dropped
    assert model.calls.count("extract_changes") == 1 and "revise_beliefs" in model.calls

    entries = {e["entry"]["entry_id"]: e
               for e in client.get(f"/v1/distillations/{did}/entries", headers=AUTH).json()}
    assert len(entries) == 2  # the revision of an unknown entry was dropped
    revised = next(e for e in entries.values() if e["before"])
    added = next(e for e in entries.values() if not e["before"])
    assert revised["entry"]["value"]["baseline"] == 0.6  # 0.3 asked, clamped to 0.8 − 0.2
    assert revised["entry"]["text"].endswith("不过和主人说话时我会收着点。")
    assert revised["reason"] == "主人说他太凶"
    # only what this evolution found, not the base entry's script quotes
    assert {q["quote"] for q in revised["evidence"]} == {"你今天太凶了", "这不像你"}
    assert added["entry"]["construct_id"] == "value.stimulation"

    tried = client.post(f"/v1/distillations/{did}/entries/{revised['entry']['entry_id']}:try",
                        headers=AUTH).json()
    assert tried["before"] and tried["after"] and "主人" in tried["situation"]

    client.patch(f"/v1/distillations/{did}/entries/{revised['entry']['entry_id']}",
                 headers=AUTH, json={"status": "accepted"})
    client.patch(f"/v1/distillations/{did}/entries/{added['entry']['entry_id']}",
                 headers=AUTH, json={"status": "rejected"})
    result = client.post(f"/v1/distillations/{did}:commit", headers=AUTH).json()  # ends chat
    assert result["k"] == 1 and result["entries"] == 1
    assert client.get("/v1/personas/xiaoxi/chats", headers=AUTH).json()[0]["status"] == "closed"

    history = client.get("/v1/personas/xiaoxi/history", headers=AUTH).json()
    assert [(h["k"], h["origin"]) for h in history] == [(1, "evolution"), (0, "construction")]
    change = history[0]["changes"]
    assert [(x["entry_id"], x["change"]) for x in change] == [
        (revised["entry"]["entry_id"], "changed")]
    refs = ",".join(change[0]["after"]["evidence_refs"])
    quotes = client.get(f"/v1/personas/xiaoxi/materials?refs={refs}", headers=AUTH).json()
    assert "你今天太凶了" in quotes.values()

    # Back to B0 as a new version; an evolution based on B0 can no longer commit.
    restored = client.post(f"/v1/personas/xiaoxi/versions/{b0['version_id']}:restore",
                           headers=AUTH).json()
    assert restored["k"] == 2 and restored["origin"] == "correction"
    assert client.post(f"/v1/personas/xiaoxi/versions/{restored['version_id']}:restore",
                       headers=AUTH).json()["error"]["code"] == "already_active"


def test_discard_and_nothing_to_learn(make):
    client = make(Recording())
    persona_from_script(client)
    draft = client.post("/v1/personas/xiaoxi/evolutions", headers=AUTH,
                        json={"persona_name": "小希"}).json()
    r = client.post(f"/v1/distillations/{draft['distillation_id']}:start", headers=AUTH,
                    json={"target": {"name": "小希"}, "persona_id": "xiaoxi"})
    assert r.json()["error"]["code"] == "no_sources"  # nothing new since B0
    assert client.post(f"/v1/distillations/{draft['distillation_id']}:discard",
                       headers=AUTH).json() == {"status": "discarded"}
    assert client.get("/v1/personas/xiaoxi/evolution:preview",
                      headers=AUTH).json()["running"] is None
    assert client.get("/v1/personas/nobody/evolution:preview", headers=AUTH).status_code == 404


def test_revision_limits():
    base = {"entry_id": "L3_x", "layer": "L3", "construct": "imago", "construct_id": "imago.friend",
            "text": "我是师兄的兄弟。", "source": "inference", "evidence_refs": ["ev:1"],
            "epistemic_status": "supported", "value": {"baseline": 0.7, "scale": "0-1"}}
    r = Revision(action="revise", entry_id="L3_x", text="和主人相处时，我也把主人当兄弟。",
                 value={"baseline": 0.1}, reason="r", evidence=["ev:9", "ev:404"])
    out = ev.apply(r, "L3", "imago", base, "L3_x", {"ev:9"}, l3_left=2)
    assert out["text"] == "我是师兄的兄弟。和主人相处时，我也把主人当兄弟。"
    assert out["value"] == base["value"] and out["evidence_refs"] == ["ev:1", "ev:9"]
    again = r.model_copy(update={"text": base["text"] + "和主人相处时，我也把主人当兄弟。"})
    assert ev.apply(again, "L3", "imago", base, "L3_x", {"ev:9"}, l3_left=2)["text"] == \
        out["text"]  # a rewrite of the whole entry appends only its new sentence
    assert ev.apply(r, "L3", "imago", base, "L3_x", {"ev:9"}, l3_left=0) is None
    assert ev.apply(r, "L3", "imago", base, "L3_x", set(), l3_left=2) is None  # no evidence
    add = Revision(action="add", construct_id="imago.pet", text="t", reason="r", evidence=["ev:9"])
    assert ev.apply(add, "L3", "imago", None, "L3_y", {"ev:9"}, l3_left=2) is None
    assert ev.owner_quote("主人：你真凶\n小希：哪有", 3, "你真凶")
    assert not ev.owner_quote("主人：你真凶\n小希：哪有", 10, "哪有")
