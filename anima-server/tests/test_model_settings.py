"""The settings page's model service: saved to a private file, used at once, key never sent back."""

import asyncio
import json
import os
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from persona_os.api.app import create_app
from persona_os.config import Settings
from persona_os.infra.openai_model import ModelCallError, OpenAIPersonaModel
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
    if os.name == "posix":  # Windows chmod does not provide POSIX private-file permissions.
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
    assert view["base_url"] == view["model"] == ""
    assert client.app.state.model.inner.client is None
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


@pytest.mark.parametrize("model", ["", "   ", "\t\n"])
def test_blank_model_is_not_saved(make, tmp_path, model):
    client = make(None)
    for endpoint in ("/v1/settings/model", "/v1/settings/model:test"):
        method = client.put if endpoint.endswith("model") else client.post
        assert method(endpoint, headers=AUTH, json={**BODY, "model": model}).status_code == 422
    assert not list(tmp_path.glob("model-*.json"))


def test_environment_and_saved_settings_survive_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONA_OPENAI_BASE_URL", "https://env.example/v1")
    monkeypatch.setenv("PERSONA_MODEL", "env-model")
    monkeypatch.setenv("PERSONA_OPENAI_API_KEY", "env-key-12345678")

    def app():
        return create_app(Settings(_env_file=None, service_tokens="t=anima",
                                  database_url=f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}"))

    with TestClient(app()) as client:
        view = client.get("/v1/settings/model", headers=AUTH).json()
        assert view["source"] == "env" and view["ready"]
        assert view["base_url"] == "https://env.example/v1" and view["model"] == "env-model"
        saved = client.put("/v1/settings/model", headers=AUTH,
                           json={**BODY, "model": "  saved-model  "}).json()
        assert saved["model"] == "saved-model" and saved["ready"]
    with TestClient(app()) as client:
        assert client.get("/v1/settings/model", headers=AUTH).json() == saved
        assert client.app.state.model.inner.client.base_url == BODY["base_url"] + "/"
    data = json.loads((tmp_path / "model-env.json").read_text())
    assert data["api_key"] == "env-key-12345678"


def test_key_only_is_not_ready(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSONA_OPENAI_API_KEY", "key-only-12345678")
    settings = Settings(_env_file=None, service_tokens="t=anima",
                        database_url=f"sqlite+aiosqlite:///{tmp_path / 'key-only.db'}")
    with TestClient(create_app(settings)) as client:
        view = client.get("/v1/settings/model", headers=AUTH).json()
        assert view["key_set"] and not view["ready"]
        assert view["base_url"] == view["model"] == ""
        response = client.post("/v1/personas/p-a/chat:say", headers=AUTH, json=SAY)
        assert response.status_code == 412
        assert response.json()["error"]["code"] == "model_not_configured"


@pytest.mark.parametrize("missing", ["openai_base_url", "model", "openai_api_key"])
@pytest.mark.parametrize("empty", ["", "   "])
def test_incomplete_adapter_never_creates_client_or_requests(monkeypatch, missing, empty):
    constructor = Mock(side_effect=AssertionError("must not construct a network client"))
    monkeypatch.setattr("persona_os.infra.openai_model.AsyncOpenAI", constructor)
    values = {"openai_base_url": BODY["base_url"], "model": BODY["model"],
              "openai_api_key": "test-key"}
    values[missing] = empty
    model = OpenAIPersonaModel(Settings(_env_file=None, **values))
    assert model.client is None

    class Payload(BaseModel):
        pass

    async def check():
        for streamed in (None, Mock()):
            with pytest.raises(ModelCallError) as error:
                await model._call("respond", Payload(), Payload, 0, streamed)
            assert error.value.kind == "not_configured" and not error.value.retryable
        await model.close()

    asyncio.run(check())
    constructor.assert_not_called()


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
