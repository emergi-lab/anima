"""stdlib-only launcher regressions; no model, database, or GUI dependencies."""
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import types
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location(
    "desktop_launcher", Path(__file__).resolve().parents[1] / "desktop_launcher.py")
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


class LauncherTests(unittest.TestCase):
    def test_data_override(self):
        with patch.dict(os.environ, {"ANIMA_DATA_DIR": "test storage"}):
            self.assertEqual(launcher.data_directory(), Path("test storage").resolve())

    def test_platform_data_paths(self):
        home = Path.home()
        with patch.dict(os.environ, {}, clear=True), \
                patch.object(launcher.sys, "platform", "darwin"), \
                patch.object(Path, "home", return_value=home):
            self.assertEqual(launcher.data_directory(), home / "Library/Application Support/Anima")
        with patch.dict(os.environ, {"LOCALAPPDATA": "C:/local"}, clear=True), \
                patch.object(launcher.sys, "platform", "win32"):
            self.assertEqual(launcher.data_directory(), Path("C:/local/Anima"))

    def test_client_layout(self):
        executable = str(Path("Anima.app/Contents/MacOS/Anima").resolve())
        with patch.object(launcher.sys, "executable", executable), \
                patch.object(launcher.sys, "platform", "darwin"):
            expected = (Path("Anima.app/Contents/Resources/client/AnimaClient.app")
                        / "Contents/MacOS/Anima")
            self.assertEqual(launcher.client_path(), expected.resolve())
        with patch.object(launcher.sys, "executable", str(Path("release/Anima.exe").resolve())), \
                patch.object(launcher.sys, "platform", "win32"):
            self.assertEqual(launcher.client_path(), Path("release/client/Anima.exe").resolve())

    def test_environment_isolated(self):
        with patch.dict(os.environ, {"PERSONA_OPENAI_API_KEY": "secret",
                                   "PERSONA_MODEL_PROXY": "bad", "PERSONA_PORT": "8030"}):
            env = launcher.child_environment(Path("space here").resolve(), 12345, "random-token")
        self.assertEqual(env["PERSONA_OPENAI_API_KEY"], "")
        self.assertNotIn("PERSONA_MODEL_PROXY", env)
        self.assertEqual(env["PERSONA_SERVICE_TOKENS"], "random-token=anima")
        self.assertEqual(env["ANIMA_API"], "http://127.0.0.1:12345")
        self.assertTrue(env["PERSONA_DATABASE_URL"].endswith("space here/data/anima.db"))

    def test_frozen_command_contains_no_token(self):
        with patch.object(launcher.sys, "frozen", True, create=True):
            self.assertEqual(launcher.server_command(), [sys.executable, "--server"])

    def test_ready_requires_authenticated_namespace(self):
        process = Mock()
        process.poll.return_value = None
        responses = [{"status": "ok"}, {"namespace": "anima"}]
        with patch.object(launcher, "request_json", side_effect=responses) as request:
            launcher.wait_ready(process, "http://127.0.0.1:12345", "token")
        self.assertEqual(request.call_args.args, ("http://127.0.0.1:12345/v1/runtime", "token"))
        process.poll.return_value = 1
        with self.assertRaisesRegex(RuntimeError, "exited"):
            launcher.wait_ready(process, "unused", "token")

    def test_timeout(self):
        process = Mock()
        process.poll.return_value = None
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            launcher.wait_ready(process, "unused", "token", timeout=0)

    def test_http_disables_proxy_and_redirect(self):
        with patch.object(launcher.urllib.request, "build_opener") as build:
            response = build.return_value.open.return_value.__enter__.return_value
            response.read.return_value = '{"status":"ok"}'
            self.assertEqual(launcher.request_json("http://127.0.0.1:1/health"), {"status": "ok"})
            self.assertEqual(build.call_args.args[0].proxies, {})
            self.assertIsInstance(build.call_args.args[1], launcher.NoRedirect)
            self.assertIsNone(build.call_args.args[1].redirect_request(
                None, None, 302, "", {}, "http://other"))

    def test_stop_escalates_only_owned_process(self):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired("owned", 10), 0]
        launcher.stop_process(process)
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        launcher.stop_process(None)

    def test_cleanup_on_readiness_failure_and_interrupt(self):
        for error in (RuntimeError("failed"), KeyboardInterrupt()):
            with self.subTest(error=type(error)), tempfile.TemporaryDirectory() as temp:
                server = Mock()
                server.poll.return_value = None
                before = Path.cwd()
                with patch.object(launcher.subprocess, "Popen", return_value=server), \
                        patch.object(launcher, "wait_ready", side_effect=error):
                    with self.assertRaises(type(error)):
                        launcher.run_session(Path(temp), [])
                server.terminate.assert_called_once()
                self.assertEqual(Path.cwd(), before)

    def test_client_args_and_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            executable = Path(temp) / "client.exe"
            executable.touch()
            server, client = Mock(), Mock()
            server.poll.return_value = None
            client.poll.return_value = 0
            client.returncode = 0
            processes = [server, client]
            with patch.object(launcher.subprocess, "Popen", side_effect=processes) as spawn, \
                    patch.object(launcher, "wait_ready"), \
                    patch.object(launcher, "client_path", return_value=executable):
                args = ["--headless", "--quit-after", "10"]
                self.assertEqual(launcher.run_session(Path(temp), args), 0)
            self.assertEqual(spawn.call_args.args[0], [str(executable), *args])
            self.assertEqual(spawn.call_args.kwargs["env"]["ANIMA_TOKEN"],
                             spawn.call_args_list[0].kwargs["env"]["ANIMA_TOKEN"])
            self.assertTrue((Path(temp) / "data").is_dir())
            self.assertTrue((Path(temp) / "logs/server.log").is_file())
            server.terminate.assert_called_once()
            client.terminate.assert_not_called()

    def test_smoke_checks_db_and_rejects_missing_token(self):
        with tempfile.TemporaryDirectory() as temp:
            server = Mock()
            server.poll.return_value = None
            denied = urllib.error.HTTPError("local", 401, "Unauthorized", {}, None)
            with patch.object(launcher.subprocess, "Popen", return_value=server) as spawn, \
                    patch.object(launcher, "wait_ready"), \
                    patch.object(launcher, "request_json", side_effect=[{"items": []}, {
                        "base_url": "", "model": "", "key_set": False, "ready": False}, denied]):
                self.assertEqual(launcher.run_session(Path(temp), [], smoke=True), 0)
                spawn.assert_called_once()
            server.terminate.assert_called_once()

    def test_smoke_rejects_prefilled_model_settings(self):
        for model in ({"base_url": "https://example.invalid/v1", "model": ""},
                      {"base_url": "", "model": "preset-model"}):
            with self.subTest(model=model), tempfile.TemporaryDirectory() as temp:
                server = Mock()
                server.poll.return_value = None
                with patch.object(launcher.subprocess, "Popen", return_value=server), \
                        patch.object(launcher, "wait_ready"), \
                        patch.object(launcher, "request_json", side_effect=[{"items": []}, {
                            **model, "key_set": False, "ready": False}]):
                    with self.assertRaisesRegex(RuntimeError, "empty model settings"):
                        launcher.run_session(Path(temp), [], smoke=True)
                server.terminate.assert_called_once()

    def test_smoke_temp_isolation_even_on_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            user_data = Path(temp) / "real-user"
            failure = RuntimeError("safe failure")
            with patch.dict(os.environ, {"ANIMA_DATA_DIR": str(user_data)}), \
                    patch.object(launcher, "run_session", side_effect=failure) as run, \
                    patch.object(launcher, "report_error") as report:
                self.assertEqual(launcher.main(["--smoke-test"]), 1)
            isolated = run.call_args.args[0]
            self.assertNotEqual(isolated, user_data)
            self.assertFalse(isolated.exists())
            self.assertFalse(user_data.exists())
            self.assertFalse(report.call_args.args[2])

    def test_external_environment_removes_frozen_paths(self):
        bundle = Path("frozen/_internal").resolve()
        system = str(Path("system/bin").resolve())
        env = {"PATH": os.pathsep.join([str(bundle), str(bundle / "lib"), system]),
               "DYLD_LIBRARY_PATH": str(bundle), "LD_LIBRARY_PATH": str(bundle),
               "LD_LIBRARY_PATH_ORIG": "original", "_PYI_APPLICATION_HOME_DIR": str(bundle),
               "PYINSTALLER_RESET_ENVIRONMENT": "1", "ANIMA_TOKEN": "keep"}
        with patch.object(launcher.sys, "_MEIPASS", str(bundle), create=True):
            clean = launcher.external_environment(env)
        self.assertEqual(clean["PATH"], system)
        self.assertEqual(clean["LD_LIBRARY_PATH"], "original")
        self.assertNotIn("DYLD_LIBRARY_PATH", clean)
        self.assertNotIn("_PYI_APPLICATION_HOME_DIR", clean)
        self.assertNotIn("PYINSTALLER_RESET_ENVIRONMENT", clean)
        self.assertEqual(clean["ANIMA_TOKEN"], "keep")
        self.assertIn("DYLD_LIBRARY_PATH", env)

    def test_frozen_server_gets_independent_bootloader(self):
        with tempfile.TemporaryDirectory() as temp:
            server = Mock()
            server.poll.return_value = None
            with patch.object(launcher.sys, "frozen", True, create=True), \
                    patch.object(launcher.subprocess, "Popen", return_value=server) as spawn, \
                    patch.object(launcher, "wait_ready", side_effect=RuntimeError("stop")):
                with self.assertRaises(RuntimeError):
                    launcher.run_session(Path(temp), [])
            self.assertEqual(spawn.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")

    def test_smoke_failure_preserves_only_redacted_logs(self):
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp) / "smoke"
            target = Path(temp) / "ci-logs"
            server = Mock()
            server.poll.return_value = None
            def fail(*args):
                (data / "logs/server.log").write_text("diagnostic secret-token", encoding="utf-8")
                (data / "data/anima.db").write_text("do not copy", encoding="utf-8")
                raise RuntimeError("startup failed")
            with patch.dict(os.environ, {"ANIMA_SMOKE_LOG_DIR": str(target)}), \
                    patch.object(launcher.subprocess, "Popen", return_value=server), \
                    patch.object(launcher.secrets, "token_urlsafe", return_value="secret-token"), \
                    patch.object(launcher, "wait_ready", side_effect=fail), \
                    patch.object(launcher.sys, "stderr", io.StringIO()):
                with self.assertRaises(RuntimeError):
                    launcher.run_session(data, [], smoke=True)
            self.assertEqual((target / "server.log").read_text(), "diagnostic [redacted]")
            self.assertEqual({p.name for p in target.iterdir()}, {"server.log", "client.log"})
            server.terminate.assert_called_once()

    def test_smoke_diagnostics_fallback_to_stderr(self):
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp)
            launcher.prepare_directory(data)
            (data / "logs/server.log").write_text("safe failure token", encoding="utf-8")
            with patch.dict(os.environ, {"ANIMA_SMOKE_LOG_DIR": ""}), \
                    patch.object(launcher.sys, "stderr", io.StringIO()) as output:
                launcher.smoke_diagnostics(data, "token")
            self.assertIn("safe failure [redacted]", output.getvalue())
            self.assertNotIn("token", output.getvalue())

    def test_directory_lock_contention_and_release(self):
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp)
            with launcher.DataDirectoryLock(data):
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    with launcher.DataDirectoryLock(data):
                        self.fail("second instance acquired the lock")
                with patch.object(launcher.subprocess, "Popen") as spawn:
                    with self.assertRaisesRegex(RuntimeError, "already running"):
                        launcher.run_session(data, [])
                    spawn.assert_not_called()
            self.assertTrue((data / ".instance.lock").is_file())
            with launcher.DataDirectoryLock(data):
                pass  # Existing file is not a lock; the descriptor was released.
            with self.assertRaisesRegex(RuntimeError, "test failure"):
                with launcher.DataDirectoryLock(data):
                    raise RuntimeError("test failure")
            with launcher.DataDirectoryLock(data):
                pass

    def test_directory_lock_released_on_process_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            script = (
                "import runpy,sys; from pathlib import Path; "
                "module=runpy.run_path(sys.argv[1]); "
                "lock=module['DataDirectoryLock'](Path(sys.argv[2])); "
                "lock.__enter__(); print('locked',flush=True); sys.stdin.read()"
            )
            process = subprocess.Popen(
                [sys.executable, "-c", script, str(launcher.SCRIPT), temp],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True,
            )
            try:
                self.assertEqual(process.stdout.readline().strip(), "locked")
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    with launcher.DataDirectoryLock(Path(temp)):
                        self.fail("lock contention was ignored")
            finally:
                process.terminate()
                process.communicate(timeout=10)
            with launcher.DataDirectoryLock(Path(temp)):
                pass

    def test_server_explicitly_disables_dotenv(self):
        settings = Mock()
        settings.return_value.host = "127.0.0.1"
        settings.return_value.port = 12345
        uvicorn, app = Mock(), Mock()
        modules = {"uvicorn": uvicorn, "persona_os": types.ModuleType("persona_os"),
                   "persona_os.api": types.ModuleType("persona_os.api"),
                   "persona_os.api.app": app, "persona_os.config": Mock(Settings=settings)}
        before = Path.cwd()
        try:
            with tempfile.TemporaryDirectory() as temp:
                with patch.dict(sys.modules, modules), patch.dict(os.environ, {
                        "ANIMA_DATA_DIR": temp, "ANIMA_TOKEN": "not-logged"}):
                    self.assertEqual(launcher.run_server(), 0)
                    settings.assert_called_once_with(_env_file=None)
                    self.assertFalse(uvicorn.run.call_args.kwargs["access_log"])
                    self.assertNotIn("not-logged", (Path(temp) / "logs/server.log").read_text())
                os.chdir(before)
        finally:
            os.chdir(before)


if __name__ == "__main__":
    unittest.main()
