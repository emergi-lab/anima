"""The settings page's model service: saved to a private file, used at once, key never sent back."""

import json
import os

from persona_os.infra.openai_model import ModelCallError
from tests.test_aftercare import AUTH, Recording
from tests.test_chat import SAY

BODY = {"base_url": "http://127.0.0.1:9/v1", "model": "my-model", "disable_thinking": False}


def test_save_mask_and_swap(make, tmp_path):
    client = make(None)  # built from settings, like the real service
    view = client.get("/v1/settings/model", headers=AUTH).json()
    assert view["source"] == "env" and view["backend"] == "fake" and not view["key_set"]

    saved = client.put("/v1/settings/model", headers=AUTH,
                       json={**BODY, "api_key": "sk-test-1234567890abcd"}).json()
    assert saved | {"backend": "openai", "model": "my-model", "key_set": True,
                    "key_hint": "…abcd", "source": "saved"} == saved
    assert "sk-test" not in json.dumps(saved)
    path = next(tmp_path.glob("model-*.json"))
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    assert json.loads(path.read_text())["api_key"] == "sk-test-1234567890abcd"
    assert type(client.app.state.model.inner).__name__ == "OpenAIPersonaModel"
    assert client.get("/v1/runtime", headers=AUTH).json()["model"] == "my-model"

    # no key in the body keeps the saved one; an empty key removes it (back to the .env backend)
    kept = client.put("/v1/settings/model", headers=AUTH, json={**BODY, "model": "other"}).json()
    assert kept["key_hint"] == "…abcd" and kept["model"] == "other"
    cleared = client.put("/v1/settings/model", headers=AUTH, json={**BODY, "api_key": ""}).json()
    assert cleared["backend"] == "fake" and not cleared["key_set"]
    assert type(client.app.state.model.inner).__name__ == "FakePersonaModel"


def test_connection_test_reports_plainly(make):
    client = make(None)
    r = client.post("/v1/settings/model:test", headers=AUTH, json=BODY).json()
    assert r == {"ok": False, "latency_ms": 0, "message": "还没有填 API Key"}
    r = client.post("/v1/settings/model:test", headers=AUTH,
                    json={**BODY, "api_key": "sk-x"}).json()  # nothing useful on port 9
    assert r["ok"] is False and r["message"] and "sk-x" not in r["message"]
    bad = client.put("/v1/settings/model", headers=AUTH, json={**BODY, "base_url": "ftp://x"})
    assert bad.status_code == 422


def test_no_key_refuses_model_work_at_once(make):
    client = make(None, backend="openai")  # a fresh clone: real backend, no key yet
    view = client.get("/v1/settings/model", headers=AUTH).json()
    assert view["backend"] == "openai" and not view["key_set"] and not view["ready"]
    did = client.post("/v1/distillations", headers=AUTH, json={"title": "x"}).json()
    for path, body in [("/v1/personas/p-a/chat:say", SAY),
                       ("/v1/personas/p-a/reflections", {"persona_name": "小希"}),
                       ("/v1/personas/p-a/evolutions", {"persona_name": "小希"}),
                       (f"/v1/distillations/{did['distillation_id']}:start", {})]:
        r = client.post(path, headers=AUTH, json=body)
        assert r.status_code == 412, (path, r.text)
        assert r.json()["error"]["code"] == "model_not_configured"
        assert "设置 → 模型服务" in r.json()["error"]["message"]
    assert client.post("/v1/personas/p-a/chat:say", json=SAY).status_code == 401  # auth first
    assert client.get("/v1/personas/p-a/chats", headers=AUTH).json() == []  # nothing stored

    saved = client.put("/v1/settings/model", headers=AUTH,
                       json={**BODY, "api_key": "sk-test-1234567890abcd"}).json()
    assert saved["ready"]
    cleared = client.put("/v1/settings/model", headers=AUTH, json={**BODY, "api_key": ""}).json()
    assert cleared["backend"] == "openai" and not cleared["ready"]


def test_a_refused_key_says_so(make):
    class Refused(Recording):
        async def respond(self, data, **kw):
            self.calls.append("respond")
            raise ModelCallError("auth", "HTTP 401", False)

    client = make(Refused())
    r = client.post("/v1/personas/p-a/chat:say", headers=AUTH, json=SAY)
    assert r.status_code == 503, r.text
    err = r.json()["error"]
    assert err["code"] == "model_rejected" and not err["retryable"]
    assert err["message"].startswith("模型服务拒绝了请求：API Key 不对或没有权限（HTTP 401）")
    assert client.app.state.model.inner.calls.count("respond") == 1  # not retried
