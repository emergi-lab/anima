"""Write docs/openapi.json from the app; tests fail when the committed file drifts."""

import json
from pathlib import Path

from persona_os.api.app import create_app
from persona_os.config import Settings

ROOT = Path(__file__).resolve().parent.parent


def render() -> str:
    app = create_app(Settings(_env_file=None, model_backend="fake"))  # schema only; ignore .env
    return json.dumps(app.openapi(), ensure_ascii=False, indent=1, sort_keys=True) + "\n"


if __name__ == "__main__":
    (ROOT / "docs" / "openapi.json").write_text(render(), encoding="utf-8")
    print("wrote docs/openapi.json")
