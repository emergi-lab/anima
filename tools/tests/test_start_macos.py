"""Launcher regressions without macOS, a model, or the user's backend/database.

ANIMA_TEST_BASH may point to Git Bash on Windows for shell integration checks.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("start_macos", ROOT / "start_macos.py")
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


def settings(host="127.0.0.1", port=8030, tokens=None):
    return SimpleNamespace(
        host=host, port=port, token_namespaces=lambda: tokens or {"private-test-token": "anima"}
    )


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="anima-launcher-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "project with spaces"
        self.server = self.root / "anima-server"
        (self.server / "docs").mkdir(parents=True)
        (self.root / "anima-pet").mkdir()
        self.schema = self.server / "docs/openapi.json"
        self.schema.write_text(
            json.dumps({"paths": {"/v1/personas/{id}/all-data": {"delete": {}}}})
        )

    def test_connection_uses_effective_port_and_namespace(self):
        api, token = launcher.connection(settings(host="::1", port=8031), {})
        self.assertEqual((api, token), ("http://[::1]:8031", "private-test-token"))
        self.assertEqual(
            launcher.connection(settings(), {"ANIMA_TOKEN": "override"})[1], "override"
        )
        with self.assertRaises(launcher.LaunchError):
            launcher.connection(settings(host="0.0.0.0"), {})
        with self.assertRaises(launcher.LaunchError):
            launcher.connection(settings(tokens={"key": "other"}), {})

    def test_lsof_no_listener_and_permission_failure_are_distinct(self):
        with patch.object(
            launcher,
            "command_output",
            return_value=SimpleNamespace(returncode=1, stdout="", stderr=""),
        ):
            self.assertEqual(launcher.listener_pids(8030), set())
        with patch.object(
            launcher,
            "command_output",
            return_value=SimpleNamespace(returncode=1, stdout="", stderr="permission denied"),
        ):
            with self.assertRaises(launcher.LaunchError):
                launcher.listener_pids(8030)

    def test_process_identity_checks_executable_and_cwd_even_with_spaces(self):
        for quoted in (False, True):
            executable = str(self.server / ".venv/bin/python")
            if quoted:
                executable = f'"{executable}"'
            command = SimpleNamespace(returncode=0, stdout=f"{executable} -m persona_os.main\n")
            cwd = SimpleNamespace(returncode=0, stdout=f"p123\nfcwd\nn{self.server}\n")
            with patch.object(launcher, "command_output", side_effect=[command, cwd]):
                self.assertTrue(launcher.is_project_backend(123, self.server))
            cwd.stdout = "p123\nfcwd\nn/another/project\n"
            with patch.object(launcher, "command_output", side_effect=[command, cwd]):
                self.assertFalse(launcher.is_project_backend(123, self.server))

    def test_unknown_listener_receives_no_token_and_is_not_stopped(self):
        with (
            patch.object(launcher, "listener_pids", return_value={123}),
            patch.object(launcher, "is_project_backend", return_value=False),
            patch.object(launcher, "read_json") as request,
            patch.object(launcher.os, "killpg", create=True) as kill,
        ):
            with self.assertRaisesRegex(launcher.LaunchError, "unrecognized"):
                launcher.supervise(settings(), None, True, self.root)
            request.assert_not_called()
            kill.assert_not_called()

    def test_old_api_and_wrong_namespace_are_rejected(self):
        for responses, reason in (
            ([{"status": "ok"}, {"namespace": "anima"}, {"paths": {}}], "outdated"),
            ([{"status": "ok"}, {"namespace": "other"}], "authentication"),
        ):
            with patch.object(launcher, "read_json", side_effect=responses):
                with self.assertRaisesRegex(launcher.LaunchError, reason):
                    launcher.verify_api("http://127.0.0.1:8030", "secret", self.schema, 123)

    def test_reused_server_only_leaves_existing_process_running(self):
        with (
            patch.object(launcher, "listener_pids", return_value={123}),
            patch.object(launcher, "is_project_backend", return_value=True),
            patch.object(launcher, "verify_api"),
            patch.object(launcher.subprocess, "Popen") as spawn,
            patch.object(launcher.os, "killpg", create=True) as kill,
        ):
            launcher.supervise(settings(), None, True, self.root)
            spawn.assert_not_called()
            kill.assert_not_called()

    def test_new_server_and_pet_arguments_environment_and_cleanup(self):
        backend = Mock(pid=100, poll=Mock(return_value=None))
        importer = Mock(pid=101, poll=Mock(return_value=0), returncode=0)
        pet = Mock(pid=102, poll=Mock(return_value=0), returncode=0)
        with (
            patch.object(launcher, "listener_pids", return_value=set()),
            patch.object(launcher, "await_ready"),
            patch.object(launcher, "verify_api"),
            patch.object(
                launcher.subprocess, "Popen", side_effect=[backend, importer, pet]
            ) as spawn,
            patch.object(launcher.os, "killpg", create=True) as kill,
            patch.dict(os.environ, {"ANIMA_TOKEN": "test-only"}),
        ):
            godot = "/Applications/Godot with spaces.app/Contents/MacOS/Godot"
            launcher.supervise(settings(port=8123), godot, False, self.root)
            commands = [call.args[0] for call in spawn.call_args_list]
            self.assertEqual(
                commands[0], [str(self.server / ".venv/bin/python"), "-m", "persona_os.main"]
            )
            self.assertIn("--quit", commands[1])
            self.assertEqual(commands[2], [godot, "--path", str(self.root / "anima-pet")])
            self.assertEqual(
                spawn.call_args_list[2].kwargs["env"]["ANIMA_API"], "http://127.0.0.1:8123"
            )
            self.assertEqual(spawn.call_args_list[2].kwargs["env"]["ANIMA_TOKEN"], "test-only")
            kill.assert_called_once_with(100, launcher.signal.SIGTERM)

    def test_pet_exit_does_not_stop_shared_backend(self):
        importer = Mock(pid=101, poll=Mock(return_value=0), returncode=0)
        pet = Mock(pid=102, poll=Mock(return_value=0), returncode=0)
        with (
            patch.object(launcher, "listener_pids", return_value={123}),
            patch.object(launcher, "is_project_backend", return_value=True),
            patch.object(launcher, "verify_api"),
            patch.object(launcher, "backend_alive", return_value=True),
            patch.object(launcher.subprocess, "Popen", side_effect=[importer, pet]),
            patch.object(launcher.os, "killpg", create=True) as kill,
        ):
            launcher.supervise(settings(), "/Godot", False, self.root)
            kill.assert_not_called()

    def test_import_failure_stops_only_owned_backend(self):
        backend = Mock(pid=100, poll=Mock(return_value=None))
        importer = Mock(pid=101, poll=Mock(return_value=2), returncode=2)
        with (
            patch.object(launcher, "listener_pids", return_value=set()),
            patch.object(launcher, "await_ready"),
            patch.object(launcher, "verify_api"),
            patch.object(launcher.subprocess, "Popen", side_effect=[backend, importer]) as spawn,
            patch.object(launcher.os, "killpg", create=True) as kill,
        ):
            with self.assertRaisesRegex(launcher.LaunchError, "import failed"):
                launcher.supervise(settings(), "/Godot", False, self.root)
            self.assertEqual(spawn.call_count, 2)
            kill.assert_called_once_with(100, launcher.signal.SIGTERM)

    def test_interrupt_during_startup_cleans_up_owned_backend(self):
        backend = Mock(pid=100, poll=Mock(return_value=None))
        with (
            patch.object(launcher, "listener_pids", return_value=set()),
            patch.object(launcher, "await_ready", side_effect=KeyboardInterrupt),
            patch.object(launcher.subprocess, "Popen", return_value=backend),
            patch.object(launcher.os, "killpg", create=True) as kill,
        ):
            with self.assertRaises(KeyboardInterrupt):
                launcher.supervise(settings(), None, True, self.root)
            kill.assert_called_once_with(100, launcher.signal.SIGTERM)

    def test_port_owner_race_is_rejected_before_authentication(self):
        backend = Mock(pid=100, poll=Mock(return_value=None))
        with (
            patch.object(launcher, "read_json", return_value={"status": "ok"}),
            patch.object(launcher, "listener_pids", return_value={999}),
        ):
            with self.assertRaisesRegex(launcher.LaunchError, "changed owners"):
                launcher.await_ready(backend, "http://127.0.0.1:8030", 8030, Path("test.log"))


class ShellTests(unittest.TestCase):
    def setUp(self):
        self.bash = os.environ.get("ANIMA_TEST_BASH") or (
            shutil.which("bash") if os.name != "nt" else None
        )
        if not self.bash:
            self.skipTest("Set ANIMA_TEST_BASH to an installed Bash to test the shell entry point")
        self.temp = tempfile.TemporaryDirectory(prefix="anima-shell-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project with spaces"
        self.server = self.root / "anima-server"
        self.bin = self.root / "test-bin"
        self.bin.mkdir(parents=True)
        (self.server / ".venv/bin").mkdir(parents=True)
        (self.root / "tools").mkdir()
        shutil.copyfile(ROOT.parent / "start.sh", self.root / "start.sh")
        (self.server / ".env.example").write_text("example\n")
        self.write(self.bin / "uname", "echo Darwin\n")
        self.write(self.bin / "uv", 'printf "uv:%s\\n" "$*"\n')
        self.write(self.server / ".venv/bin/python", 'printf "arg:<%s>\\n" "$@"\n')
        self.env = dict(
            os.environ, PATH=str(self.bin) + os.pathsep + os.environ["PATH"], GODOT_BIN=""
        )

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/bash\n" + text, encoding="utf-8", newline="\n")
        path.chmod(0o755)

    def run_shell(self, *args):
        # Git Bash prepends its own bin directories on startup; put test doubles first afterwards.
        return subprocess.run(
            [
                self.bash,
                "-c",
                'test_bin=$(cd -- "$1" && pwd -P); export PATH="$test_bin:$PATH"; '
                'shift; exec /bin/bash "$@"',
                "--",
                self.bin.as_posix(),
                (self.root / "start.sh").as_posix(),
                *args,
            ],
            env=self.env,
            cwd=self.temp.name,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_help_and_invalid_arguments_do_not_start_dependencies(self):
        help_result = self.run_shell("--help")
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("Usage:", help_result.stdout)
        invalid = self.run_shell("--godot-path")
        self.assertNotEqual(invalid.returncode, 0)
        self.assertNotIn("uv:", invalid.stdout)

    def test_server_only_needs_no_godot_and_keeps_existing_env(self):
        env_file = self.server / ".env"
        env_file.write_text("keep-this\n")
        result = self.run_shell("--server-only")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("uv:sync --locked", result.stdout)
        self.assertIn("arg:<--server-only>", result.stdout)
        self.assertEqual(env_file.read_text(), "keep-this\n")

    def test_relative_bundle_and_executable_paths_preserve_spaces(self):
        bundle = Path(self.temp.name) / "Godot test.app"
        executable = bundle / "Contents/MacOS/Godot"
        self.write(executable, "exit 0\n")
        for location in (bundle.name, executable.as_posix()):
            with self.subTest(location=location):
                result = self.run_shell("--godot-path", location)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("Godot test.app/Contents/MacOS/Godot>", result.stdout)
        self.assertEqual((self.server / ".env").read_text(), "example\n")

    def test_godot_environment_directory_and_invalid_path(self):
        folder = Path(self.temp.name) / "Godot downloads"
        self.write(folder / "Godot.app/Contents/MacOS/Godot", "exit 0\n")
        self.env["GODOT_BIN"] = folder.as_posix()
        result = self.run_shell()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Godot downloads/Godot.app/Contents/MacOS/Godot>", result.stdout)
        self.env["GODOT_BIN"] = "missing-godot.app"
        result = self.run_shell()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Godot executable not found", result.stderr)
        self.assertNotIn("uv:", result.stdout)


if __name__ == "__main__":
    unittest.main()
