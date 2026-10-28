"""Process supervision for start.sh. Configuration/credentials stay inside this process.

No shell evaluation, proxying of localhost requests, or automatic termination of port owners.
Only backend/Godot processes created by this launcher are stopped when it exits.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


class LaunchError(Exception):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A local server must not forward the service token to another endpoint.
        return None


def message(text: str) -> None:
    print(f"[Anima] {text}", flush=True)


def read_json(url: str, token: str = "", timeout: float = 2.0):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    opener = build_opener(ProxyHandler({}), NoRedirect())
    with opener.open(Request(url, headers=headers), timeout=timeout) as response:
        return json.load(response)


def connection(settings, env: dict[str, str]) -> tuple[str, str]:
    if settings.host not in ("127.0.0.1", "localhost", "::1"):
        raise LaunchError("For desktop use, set PERSONA_HOST=127.0.0.1 in anima-server/.env.")
    host = "[::1]" if settings.host == "::1" else settings.host
    token = env.get("ANIMA_TOKEN") or next(
        (key for key, ns in settings.token_namespaces().items() if ns == "anima"), ""
    )
    if not token:
        raise LaunchError("No token for namespace anima. Check PERSONA_SERVICE_TOKENS.")
    return f"http://{host}:{settings.port}", token


def command_output(args: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LaunchError(f"Cannot run {Path(args[0]).name} to verify the local backend.") from exc


def listener_pids(port: int) -> set[int]:
    result = command_output(["/usr/sbin/lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fp"])
    # lsof returns 1 (and no output) when there is no matching listener.
    if result.returncode not in (0, 1) or (result.returncode and result.stderr.strip()):
        raise LaunchError(f"Cannot inspect port {port}; check lsof permissions.")
    return {
        int(line[1:])
        for line in result.stdout.splitlines()
        if line.startswith("p") and line[1:].isdigit()
    }


def is_project_backend(pid: int, server_dir: Path) -> bool:
    result = command_output(["/bin/ps", "-ww", "-p", str(pid), "-o", "command="])
    if result.returncode:
        return False
    command = result.stdout.strip()
    # ps may show the executable with or without quotes; do not split on spaces in a path.
    executable = str(server_dir / ".venv/bin/python")
    expected = (f"{executable} -m persona_os.main", f'"{executable}" -m persona_os.main')
    if command not in expected:
        return False
    cwd = command_output(["/usr/sbin/lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"])
    return cwd.returncode == 0 and any(
        line.startswith("n") and Path(line[1:]).resolve() == server_dir.resolve()
        for line in cwd.stdout.splitlines()
    )


def verify_api(api: str, token: str, schema_file: Path, pid: int) -> None:
    try:
        if read_json(api + "/health").get("status") != "ok":
            raise ValueError("unexpected health")
        if read_json(api + "/v1/runtime", token).get("namespace") != "anima":
            raise ValueError("unexpected namespace")
        running = read_json(api + "/openapi.json")["paths"]
        expected = json.loads(schema_file.read_text(encoding="utf-8"))["paths"]
        missing = [
            f"{method.upper()} {path}"
            for path, route in expected.items()
            for method in route
            if method in METHODS and method not in running.get(path, {})
        ]
    except (OSError, URLError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise LaunchError(
            f"Backend PID {pid} failed its health, authentication or API check. "
            "Check ANIMA_TOKEN and anima-server/.env, or restart that backend."
        ) from exc
    if missing:
        raise LaunchError(
            f"Backend PID {pid} is outdated; missing API: {', '.join(missing)}. "
            "Stop that backend, then run this launcher again. Reopening only the pet "
            "will not update the server."
        )


def stop_owned(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
    except ProcessLookupError:
        pass


def backend_alive(owned: subprocess.Popen | None, shared_pid: int | None) -> bool:
    if owned is not None:
        return owned.poll() is None
    try:
        os.kill(shared_pid, 0)
        return True
    except (ProcessLookupError, TypeError):
        return False


def await_ready(process: subprocess.Popen, api: str, port: int, log_path: Path) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise LaunchError(f"Backend exited. See {log_path}")
        try:
            if read_json(api + "/health").get("status") == "ok":
                # Another launcher could have won a race to bind the port.
                if listener_pids(port) != {process.pid}:
                    raise LaunchError(f"Port {port} changed owners during startup. Try again.")
                return
        except (URLError, OSError, ValueError, AttributeError):
            pass
        time.sleep(0.25)
    raise LaunchError(f"Backend did not become ready in 60 seconds. See {log_path}")


def supervise(settings, godot: str | None, server_only: bool, root: Path = ROOT) -> None:
    server_dir, pet_dir = root / "anima-server", root / "anima-pet"
    api, token = connection(settings, os.environ)
    child_env = dict(os.environ, ANIMA_API=api, ANIMA_TOKEN=token)
    owners = listener_pids(settings.port)
    shared_pid = None
    owned = None
    pet = None
    importer = None
    try:
        if owners:
            if len(owners) != 1 or not is_project_backend(next(iter(owners)), server_dir):
                raise LaunchError(
                    f"Port {settings.port} is occupied by another or unrecognized process. "
                    "Stop that service or change PERSONA_PORT in anima-server/.env."
                )
            shared_pid = next(iter(owners))
            verify_api(api, token, server_dir / "docs/openapi.json", shared_pid)
            message(f"Reusing existing project backend (PID {shared_pid}): {api}")
        else:
            log_dir = server_dir / "logs"
            log_dir.mkdir(exist_ok=True)
            prefix = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            error_log = log_dir / f"{prefix}.err.log"
            with (
                (log_dir / f"{prefix}.out.log").open("ab") as stdout,
                error_log.open("ab") as stderr,
            ):
                owned = subprocess.Popen(
                    [str(server_dir / ".venv/bin/python"), "-m", "persona_os.main"],
                    cwd=server_dir,
                    env=child_env,
                    stdout=stdout,
                    stderr=stderr,
                    start_new_session=True,
                )
            await_ready(owned, api, settings.port, error_log)
            verify_api(api, token, server_dir / "docs/openapi.json", owned.pid)
            message(f"Backend ready: {api}/docs")
            message(f"Logs: {log_dir}")
        if server_only:
            if shared_pid is not None:
                message("Existing backend is healthy; leaving it running.")
                return
            message("Press Ctrl+C to stop.")
            while owned.poll() is None:
                time.sleep(0.25)
            raise LaunchError("Backend exited unexpectedly. Check anima-server/logs/.")
        message("Importing Godot assets (first launch may take a while)...")
        importer = subprocess.Popen(
            [godot, "--headless", "--path", str(pet_dir), "--editor", "--import", "--quit"],
            cwd=pet_dir,
            env=child_env,
            start_new_session=True,
        )
        while importer.poll() is None:
            if not backend_alive(owned, shared_pid):
                raise LaunchError("Backend exited unexpectedly during resource import.")
            time.sleep(0.25)
        if importer.returncode:
            raise LaunchError(f"Godot asset import failed (exit {importer.returncode}).")
        if not backend_alive(owned, shared_pid):
            raise LaunchError("Backend exited before the pet could start.")
        message(
            "Opening desktop pet. Exit from its menu to stop both services."
            if owned
            else "Opening desktop pet. The shared backend will remain running when the pet exits."
        )
        pet = subprocess.Popen(
            [godot, "--path", str(pet_dir)], cwd=pet_dir, env=child_env, start_new_session=True
        )
        while pet.poll() is None:
            if not backend_alive(owned, shared_pid):
                raise LaunchError("Backend exited unexpectedly. Restart the launcher.")
            time.sleep(0.25)
        if pet.returncode:
            raise LaunchError(f"Godot exited with code {pet.returncode}.")
    finally:
        stop_owned(pet)
        stop_owned(importer)
        stop_owned(owned)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--godot-path")
    parser.add_argument("--server-only", action="store_true")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("Use start.bat on Windows; this helper is for macOS.")
    if not args.server_only and (not args.godot_path or not os.access(args.godot_path, os.X_OK)):
        parser.error("An executable --godot-path is required unless --server-only is used.")
    os.chdir(ROOT / "anima-server")

    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    try:
        from persona_os.config import Settings

        try:
            settings = Settings()
        except ValueError as exc:
            # Pydantic errors can contain the rejected value, including service credentials.
            raise LaunchError("Cannot read backend settings. Check anima-server/.env.") from exc
        supervise(settings, args.godot_path, args.server_only)
        return 0
    except KeyboardInterrupt:
        message("Stopped.")
        return 130
    except (LaunchError, OSError) as exc:
        message(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
