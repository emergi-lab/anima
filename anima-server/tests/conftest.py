import os

# In-process TestClient traffic must never go through a developer's proxy.
for name in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
    os.environ.pop(name, None)


import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pydantic import SecretStr  # noqa: E402

from persona_os.api.app import create_app  # noqa: E402
from persona_os.config import Settings  # noqa: E402


@pytest.fixture
def make(tmp_path):
    """App with two imported personas (p-a, p-b) on a file SQLite database, for a given model.
    A file (not in-memory) database gives each session its own connection, so background jobs
    and requests interleave as they do in production."""
    from tests.test_aftercare import AUTH, persona

    clients = []

    def _make(model, backend="fake"):
        db = tmp_path / f"persona-{len(clients)}.db"
        # explicit, so a developer's .env (real key, openai backend) never reaches a test
        settings = Settings(_env_file=None, service_tokens=SecretStr("t=anima"),
                            database_url=f"sqlite+aiosqlite:///{db}",
                            model_file=str(tmp_path / f"model-{len(clients)}.json"),
                            model_backend=backend, openai_api_key=SecretStr(""))
        client = TestClient(create_app(settings, model=model))
        client.__enter__()
        clients.append(client)
        assert client.post("/v1/personas:import", headers=AUTH, json={
            "personas": [persona("p-a"), persona("p-b")]}).status_code == 200
        return client
    yield _make
    for client in clients:
        try:
            client.portal.call(client.app.state.aftercare.idle)
        finally:
            client.__exit__(None, None, None)


@pytest.fixture(autouse=True)
def _private_model_file(tmp_path, monkeypatch):
    """Isolate process settings and keep saved model settings in the test directory."""
    for name in tuple(os.environ):
        if name.upper().startswith("PERSONA_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("PERSONA_MODEL_FILE", str(tmp_path / "model-env.json"))
