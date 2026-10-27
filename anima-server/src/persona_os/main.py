from __future__ import annotations

import argparse

import uvicorn

from persona_os.config import Settings


def run() -> None:
    parser = argparse.ArgumentParser(description="anima-server API")
    parser.add_argument("--reload", action="store_true", help="development hot reload")
    args = parser.parse_args()
    settings = Settings()
    uvicorn.run("persona_os.api.app:create_app", factory=True, host=settings.host,
                port=settings.port, reload=args.reload,
                reload_dirs=["src"] if args.reload else None)


if __name__ == "__main__":
    run()
