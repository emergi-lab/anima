"""Frozen desktop entry point; no Python/uv/Godot installation is required.

--smoke-test checks the backend in disposable storage, without a GUI or model calls.
Other arguments (optionally after --) are forwarded to the exported Godot client.
"""
from __future__ import annotations

import errno
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

SCRIPT = Path(__file__).resolve()


def data_directory() -> Path:
    override = os.environ.get("ANIMA_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        return (Path(local) if local else Path.home() / "AppData/Local") / "Anima"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Anima"
    return Path.home() / ".local/share/Anima"


def client_path() -> Path:
    executable = Path(sys.executable).resolve()
    if sys.platform == "darwin":
        return (executable.parent.parent / "Resources/client/AnimaClient.app"
                / "Contents/MacOS/AnimaClient")
    return executable.parent / "client/Anima.exe"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def child_environment(data: Path, port: int, token: str) -> dict[str, str]:
    # Never inherit development service credentials, model keys, or storage settings.
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith("PERSONA_")}
    env.update({
        "ANIMA_DATA_DIR": str(data),
        "ANIMA_API": f"http://127.0.0.1:{port}",
        "ANIMA_TOKEN": token,
        "PERSONA_APP_ENV": "production",
        "PERSONA_HOST": "127.0.0.1",
        "PERSONA_PORT": str(port),
        "PERSONA_SERVICE_TOKENS": f"{token}=anima",
        "PERSONA_DATABASE_URL": "sqlite+aiosqlite:///" + (data / "data/anima.db").as_posix(),
        "PERSONA_MODEL_FILE": str(data / "data/model.json"),
        "PERSONA_MODEL_BACKEND": "openai",
        "PERSONA_OPENAI_API_KEY": "",
        "PYTHONNOUSERSITE": "1",
    })
    return env


def prepare_directory(data: Path) -> None:
    (data / "data").mkdir(parents=True, exist_ok=True)
    (data / "logs").mkdir(parents=True, exist_ok=True)


def external_environment(env: dict[str, str]) -> dict[str, str]:
    """Undo frozen runtime search paths for Godot (not the same-exe server)."""
    clean = {k: v for k, v in env.items()
             if not k.startswith(("_PYI_", "PYINSTALLER_"))}
    bundle = getattr(sys, "_MEIPASS", None)
    for name in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH"):
        original = clean.pop(name + "_ORIG", None)
        clean.pop(name, None)
        if original is not None:
            clean[name] = original
    if bundle:
        root = Path(bundle).resolve()
        for name in ("PATH", "DYLD_FRAMEWORK_PATH"):
            if name in clean:
                clean[name] = os.pathsep.join(
                    entry for entry in clean[name].split(os.pathsep)
                    if entry and not Path(entry).resolve().is_relative_to(root)
                )
    return clean


def spawn_client(command: list[str], **kwargs):
    # Windows DLL search state is process-global, not part of env. The launcher
    # is single-threaded; restore its exact previous state immediately after spawn.
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return subprocess.Popen(command, **kwargs)
    import ctypes
    kernel = ctypes.windll.kernel32
    size = kernel.GetDllDirectoryW(0, None)
    previous = ctypes.create_unicode_buffer(size + 1)
    kernel.GetDllDirectoryW(len(previous), previous)
    if not kernel.SetDllDirectoryW(None):
        raise OSError("Cannot reset DLL search path for client.")
    try:
        return subprocess.Popen(command, **kwargs)
    finally:
        kernel.SetDllDirectoryW(previous.value or None)


def smoke_diagnostics(data: Path, token: str) -> None:
    """Preserve only disposable smoke logs, never its DB/model or user data."""
    destination = os.environ.get("ANIMA_SMOKE_LOG_DIR")
    try:
        target = Path(destination).expanduser().resolve() if destination else None
        if target:
            target.mkdir(parents=True, exist_ok=True)
        for name in ("server.log", "client.log"):
            source = data / "logs" / name
            if not source.is_file():
                continue
            text = source.read_text(encoding="utf-8", errors="replace").replace(token, "[redacted]")
            if target:
                (target / name).write_text(text, encoding="utf-8")
            elif sys.stderr is not None and text:
                print(f"Smoke {name} (last 32 KiB):\n{text[-32768:]}", file=sys.stderr)
        if target and sys.stderr is not None:
            print(f"Smoke diagnostics: {target}", file=sys.stderr)
    except OSError:
        if sys.stderr is not None:
            print("Could not preserve smoke diagnostics.", file=sys.stderr)


def server_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--server"]
    return [sys.executable, str(SCRIPT), "--server"]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(url: str, token: str | None = None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with opener.open(urllib.request.Request(url, headers=headers), timeout=1) as response:
        return json.load(response)


def wait_ready(server, api: str, token: str, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError("Backend exited before becoming ready; see server.log.")
        try:
            health = request_json(api + "/health")
            runtime = request_json(api + "/v1/runtime", token)
            if health.get("status") == "ok" and runtime.get("namespace") == "anima":
                if server.poll() is not None:
                    raise RuntimeError("Backend exited during startup.")
                return
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(0.1)
    raise RuntimeError("Backend readiness timed out; see server.log.")


def stop_process(process) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


class DataDirectoryLock:
    """OS-owned lock; a stale lock file never prevents the next launch."""

    def __init__(self, data: Path):
        self.path = data / ".instance.lock"
        self.stream = None

    def __enter__(self):
        stream = self.path.open("a+b")
        try:
            if sys.platform == "win32":
                import msvcrt
                if self.path.stat().st_size == 0:
                    stream.write(b"\0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException as exc:
            stream.close()
            if isinstance(exc, OSError) and exc.errno in (
                errno.EACCES, errno.EAGAIN, errno.EDEADLK,
            ):
                raise RuntimeError(
                    "Anima is already running with this data directory. "
                    "Please close the existing instance before starting another."
                ) from None
            raise
        self.stream = stream
        return self

    def __exit__(self, *_):
        # Closing the descriptor releases both flock and msvcrt locks, including
        # on exceptions. The OS also releases it if the launcher is terminated.
        self.stream.close()
        self.stream = None


def run_session(data: Path, client_args: list[str], smoke: bool = False) -> int:
    data = data.resolve()
    prepare_directory(data)
    with DataDirectoryLock(data):
        return _run_session(data, client_args, smoke)


def _run_session(data: Path, client_args: list[str], smoke: bool) -> int:
    original_cwd = Path.cwd()
    server = client = None
    env = child_environment(data, free_port(), secrets.token_urlsafe(32))
    server_env = env.copy()
    if getattr(sys, "frozen", False):
        # An independently initialized bootloader, not a multiprocessing worker.
        # Not strictly necessary for onedir children confined to the parent lifetime,
        # but explicit reset avoids inheriting PyInstaller's private process level.
        server_env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
    try:
        os.chdir(data)
        with (data / "logs/server.log").open("a", encoding="utf-8") as server_log, \
                (data / "logs/client.log").open("a", encoding="utf-8") as client_log:
            try:
                server = subprocess.Popen(server_command(), env=server_env, cwd=data,
                                          stdin=subprocess.DEVNULL, stdout=server_log,
                                          stderr=subprocess.STDOUT, **flags)
                wait_ready(server, env["ANIMA_API"], env["ANIMA_TOKEN"])
                if smoke:
                    # A real DB-backed endpoint and negative authentication check.
                    roster = request_json(env["ANIMA_API"] + "/v1/personas", env["ANIMA_TOKEN"])
                    if roster.get("items") != []:
                        raise RuntimeError("Smoke database was not empty.")
                    try:
                        request_json(env["ANIMA_API"] + "/v1/runtime")
                    except urllib.error.HTTPError as exc:
                        if exc.code != 401:
                            raise RuntimeError("Unexpected authentication response.") from None
                    else:
                        raise RuntimeError("Backend accepted an unauthenticated request.")
                    return 0
                executable = client_path()
                if not executable.is_file():
                    raise RuntimeError(f"Bundled client is missing: {executable}")
                client = spawn_client(
                    [str(executable), *client_args], env=external_environment(env), cwd=data,
                    stdin=subprocess.DEVNULL, stdout=client_log,
                    stderr=subprocess.STDOUT, **flags,
                )
                while client.poll() is None:
                    if server.poll() is not None:
                        raise RuntimeError("Backend stopped unexpectedly; see server.log.")
                    time.sleep(0.2)
                if client.returncode:
                    raise RuntimeError(
                        f"Client exited with code {client.returncode}; see client.log."
                    )
                return 0
            finally:
                # Only Popen handles created above are ever terminated, never a port/PID lookup.
                try:
                    stop_process(client)
                finally:
                    stop_process(server)
    except BaseException:
        if smoke:
            # Streams are closed and children reaped before reading the logs.
            os.chdir(original_cwd)
            smoke_diagnostics(data, env["ANIMA_TOKEN"])
        raise
    finally:
        os.chdir(original_cwd)


def run_server() -> int:
    data = data_directory()
    prepare_directory(data)
    os.chdir(data)
    # PyInstaller windowed executables have no sys.stderr; uvicorn requires a stream.
    with (data / "logs/server.log").open("a", encoding="utf-8", buffering=1) as stream:
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = stream
        try:
            import uvicorn
            from persona_os.api.app import create_app
            from persona_os.config import Settings

            # Explicitly suppress dotenv, including a .env accidentally placed in user data.
            settings = Settings(_env_file=None)
            if settings.host != "127.0.0.1" or not os.environ.get("ANIMA_TOKEN"):
                raise RuntimeError("--server is an internal launcher command.")
            uvicorn.run(create_app(settings), host="127.0.0.1", port=settings.port,
                        loop="asyncio", http="h11", ws="none", access_log=False,
                        use_colors=False)
            return 0
        except Exception as exc:
            # Avoid validation errors dumping environment values (including credentials).
            print(f"Backend startup/runtime failed ({type(exc).__name__}).", file=stream)
            # Stack locations (without locals) identify missing frozen imports without
            # dumping settings/validation errors containing credentials.
            import traceback
            traceback.print_tb(exc.__traceback__, file=stream)
            if isinstance(exc, ImportError):
                print(f"Import failed: {exc}", file=stream)
            return 1
        finally:
            sys.stdout, sys.stderr = old_out, old_err


def report_error(data: Path, message: str, gui: bool) -> None:
    # Callers supply only safe messages, never exception reprs/env/commands containing secrets.
    text = f"{message}\nLogs: {data / 'logs'}"
    try:
        (data / "logs").mkdir(parents=True, exist_ok=True)
        with (data / "logs/launcher.log").open("a", encoding="utf-8") as log:
            log.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text + "\n")
    except OSError:
        pass
    if sys.stderr is not None:
        print(text, file=sys.stderr)
    if not gui:
        return
    try:
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, "Anima", 0x10)
        elif sys.platform == "darwin":
            subprocess.run(["/usr/bin/osascript", "-e",
                            'on run argv\ndisplay alert "Anima" message (item 1 of argv)'
                            ' as critical\nend run', text], check=False, timeout=60,
                           env=external_environment(dict(os.environ)))
    except (OSError, subprocess.TimeoutExpired):
        pass


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--server"]:
        return run_server()
    smoke = "--smoke-test" in args
    args = [arg for arg in args if arg != "--smoke-test"]
    if args[:1] == ["--"]:
        args.pop(0)
    gui = not smoke and "--headless" not in args
    old_handler = signal.getsignal(signal.SIGTERM)
    def interrupted(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    data = data_directory()
    def execute(directory: Path) -> int:
        try:
            return run_session(directory, [] if smoke else args, smoke=smoke)
        except KeyboardInterrupt:
            return 130
        except Exception as exc:
            # Do not print arbitrary library exceptions (they can contain credentials).
            message = (str(exc) if type(exc) is RuntimeError
                       else f"Launch failed ({type(exc).__name__}).")
            report_error(directory, message, gui)
            return 1
    try:
        if smoke:
            with tempfile.TemporaryDirectory(prefix="anima-smoke-") as temp:
                return execute(Path(temp))
        return execute(data)
    finally:
        signal.signal(signal.SIGTERM, old_handler)


if __name__ == "__main__":
    raise SystemExit(main())
