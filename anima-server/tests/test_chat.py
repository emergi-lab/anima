"""A2: pet chat sessions over the engine — turns, emotion, ending, idle sweep, recall."""

from datetime import timedelta

from fastapi.testclient import TestClient

from persona_os.infra.openai_model import ModelCallError
from tests.test_aftercare import AUTH, Recording, settled

SAY = {"text": "今天好累啊", "persona_name": "小希"}


def say(client: TestClient, text: str = "今天好累啊", pid: str = "p-a") -> dict:
    r = client.post(f"/v1/personas/{pid}/chat:say", headers=AUTH, json={**SAY, "text": text})
    assert r.status_code == 200, r.text
    return r.json()


def test_chat_turns_end_and_recall(make):
    model = Recording()
    client = make(model)
    first = say(client)
    second = say(client, "你在干嘛")
    sid = first["session_id"]
    assert second["session_id"] == sid
    assert first["reply"]["speaker"] == "persona" and first["reply"]["emotion"] == "calm"
    assert "usage" not in first and model.calls[:2] == ["respond", "respond"]
    client.portal.call(client.app.state.chat.settled)  # the basis is stored after the reply
    assert client.get(f"/v1/actions/{second['reply']['action_id']}/explanation",
                      headers=AUTH).status_code == 200
    state = client.get(f"/v1/actions/{second['reply']['action_id']}/state", headers=AUTH).json()
    assert state["persona_id"] == "p-a" and "L3" in state["state"] and state["state_issue"] is None

    history = client.get("/v1/personas/p-a/chats", headers=AUTH).json()
    assert [t["speaker"] for t in history[0]["turns"]] == ["owner", "persona"] * 2
    assert [t["seq"] for t in history[0]["turns"]] == [0, 1, 2, 3]
    assert history[0]["status"] == "open"

    ended = client.post("/v1/personas/p-a/chat:end", headers=AUTH).json()
    assert ended["status"] == "closed"
    assert client.post("/v1/personas/p-a/chat:end", headers=AUTH).json() is None
    assert settled(client, sid)[0]["status"] == "done"

    third = say(client, "我回来了")
    assert third["session_id"] != sid and third["reply"]["seq"] == 1
    assert model.recalled[-1] and model.recalled[-1][0]["kind"] == "episodic"
    assert [s["session_id"] for s in client.get("/v1/personas/p-a/chats",
                                                headers=AUTH).json()] == [third["session_id"], sid]


def test_idle_session_is_swept_and_failures_are_reported(make):
    class Flaky(Recording):
        def __init__(self):
            super().__init__()
            self.down = 2

        async def respond(self, data, **kw):
            if self.down:
                self.down -= 1
                raise ModelCallError("timeout", "slow", True)
            return await super().respond(data, **kw)

    client = make(Flaky())
    r = client.post("/v1/personas/p-a/chat:say", headers=AUTH, json=SAY)
    assert r.status_code == 503 and r.json()["error"]["retryable"]
    reply = say(client, "还在吗")  # the unanswered line stays in the session
    assert reply["reply"]["seq"] == 2
    assert client.post("/v1/personas/nobody/chat:say", headers=AUTH,
                       json=SAY).status_code == 404

    chat = client.app.state.chat
    assert client.portal.call(chat.sweep) == 0
    chat.idle = timedelta(0)
    assert client.portal.call(chat.sweep) == 1
    assert client.get("/v1/personas/p-a/chats", headers=AUTH).json()[0]["status"] == "closed"


def test_one_call_reply_is_checked_and_redone(make):
    class Moody(Recording):
        async def respond(self, data, on_final=None):
            first = "respond" not in self.calls  # the first try forgets the emotion

            async def shown(final):
                await on_final(final.model_copy(update={"extra": {}}) if first else final)

            result = await super().respond(data, on_final=shown if on_final else None)
            if first:
                result.value.final.extra = {}
            return result

    model = Moody()
    reply = say(make(model))
    assert model.calls == ["respond", "respond"]
    assert reply["reply"]["emotion"] == "calm"


def test_line_is_shown_before_the_rest_and_kept_if_the_rest_fails(make):
    """The reply returns once the line is written; if the output then breaks, the shown line is
    still what gets stored (with a note instead of a state), never a different one."""
    import asyncio

    gate = asyncio.Event()

    class Slow(Recording):
        async def respond(self, data, on_final=None):
            result = await super().respond(data)  # the line, without telling yet
            if on_final is not None:
                await on_final(result.value.final)
            await gate.wait()  # the state and basis are still being written
            if self.calls.count("respond") == 2:  # the second reply breaks after its line
                raise ModelCallError("invalid_output", "cut off", True)
            return result

    client = make(Slow())
    chat = client.app.state.chat
    first = say(client)  # returns while the model is still "writing"
    assert first["reply"]["text"] and first["reply"]["emotion"] == "calm"
    action = first["reply"]["action_id"]
    assert client.get(f"/v1/actions/{action}/explanation", headers=AUTH).status_code == 404
    client.portal.call(gate.set)
    client.portal.call(chat.settled)
    assert client.get(f"/v1/actions/{action}/explanation", headers=AUTH).status_code == 200
    turns = client.get("/v1/personas/p-a/chats", headers=AUTH).json()[0]["turns"]
    assert [t["speaker"] for t in turns] == ["owner", "persona"]

    second = say(client, "再说一句")  # the rest of this output fails after the line is shown
    client.portal.call(chat.settled)
    turns = client.get("/v1/personas/p-a/chats", headers=AUTH).json()[0]["turns"]
    assert turns[-1]["text"] == second["reply"]["text"] and turns[-1]["seq"] == 3
    why = client.get(f"/v1/actions/{second['reply']['action_id']}/explanation",
                     headers=AUTH).json()
    assert why["basis"] == {}
    kept = client.get(f"/v1/actions/{second['reply']['action_id']}/state", headers=AUTH).json()
    assert kept["state_issue"] and kept["state"]["L3"] == {}


def test_emotion_words_are_mapped_not_rejected():
    from persona_os.chat.service import _emotion

    assert [_emotion(w) for w in ("happy", "Concerned", "shy", "grumpy", None)] == [
        "happy", "worried", "embarrassed", "calm", None]
