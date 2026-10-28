import contextlib
import hashlib
import io
import os
import stat
import sys
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import setup_godot as godot


class SetupGodotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def make_zip(self, entries):
        path = self.root / "archive.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in entries:
                archive.writestr(name, data)
        return path

    def test_checksums(self):
        digest = hashlib.sha512(b"editor").hexdigest()
        self.assertEqual(
            godot.parse_checksums(f"{digest.upper()} *editor.zip\n"), {"editor.zip": digest}
        )
        path = self.root / "editor.zip"
        path.write_bytes(b"editor")
        godot.verify_checksum(path, digest)
        with self.assertRaisesRegex(ValueError, "mismatch"):
            godot.verify_checksum(path, "0" * 128)
        for text in ("invalid", f"{digest} a\n{digest} a"):
            with self.assertRaises(ValueError):
                godot.parse_checksums(text)

    def test_unsafe_paths(self):
        for name in (
            "../escape",
            "/absolute",
            "C:/escape",
            "a/../../x",
            "a\\x",
            "NUL",
            "a./b",
            "a:stream",
            "",
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                godot.safe_zip_path(name)
        self.assertEqual(
            godot.safe_zip_path("Godot.app/Contents/MacOS/Godot"),
            ("Godot.app", "Contents", "MacOS", "Godot"),
        )
        path = self.make_zip([("../escape", b"bad")])
        with zipfile.ZipFile(path) as archive, self.assertRaises(ValueError):
            godot.validate_zip(archive)

    def symlink(self, name):
        info = zipfile.ZipInfo(name)
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        return info

    def test_symlinks(self):
        for entries in (
            [(self.symlink("link"), "../outside")],
            [(self.symlink("link"), "/outside")],
            [(self.symlink("link"), "target"), ("link/file", "bad")],
            [(self.symlink("a"), "b"), (self.symlink("b"), "a")],
            [
                ("a/file", "ok"),
                (self.symlink("a/link"), ".."),
                (self.symlink("escape"), "a/link/../outside"),
            ],
        ):
            path = self.make_zip(entries)
            with zipfile.ZipFile(path) as archive, self.assertRaises(ValueError):
                godot.validate_zip(archive)
        path = self.make_zip(
            [
                ("Versions/A/file", "ok"),
                (self.symlink("Versions/Current"), "A"),
                (self.symlink("file"), "Versions/Current/file"),
            ]
        )
        with zipfile.ZipFile(path) as archive:
            godot.validate_zip(archive)

    def test_platform_paths_and_executable_preference(self):
        with mock.patch.dict(os.environ, {"APPDATA": str(self.root)}):
            self.assertEqual(
                godot.templates_dir("4.7.2", "win32"),
                self.root / "Godot/export_templates/4.7.2.stable",
            )
        with mock.patch.object(Path, "home", return_value=self.root):
            self.assertEqual(
                godot.templates_dir("4.7.2", "darwin"),
                self.root / "Library/Application Support/Godot/export_templates/4.7.2.stable",
            )
        self.assertEqual(godot.platform_config("4.7.2", "darwin")[1], ("macos.zip",))
        with self.assertRaises(ValueError):
            godot.platform_config("4.7.2", "linux")
        gui = self.root / "Godot_v4.7.2-stable_win64.exe"
        console = self.root / "Godot_v4.7.2-stable_win64_console.exe"
        gui.touch()
        self.assertEqual(godot.editor_executable(self.root, "4.7.2", "win32"), gui.resolve())
        console.touch()
        self.assertEqual(godot.editor_executable(self.root, "4.7.2", "win32"), console.resolve())
        mac = self.root / "Godot.app/Contents/MacOS/Godot"
        mac.parent.mkdir(parents=True)
        mac.touch()
        self.assertEqual(godot.editor_executable(self.root, "4.7.2", "darwin"), mac.resolve())

    def test_download_cache_and_retry(self):
        path = self.root / "download"
        with (
            mock.patch.object(
                godot.urllib.request,
                "urlopen",
                side_effect=[urllib.error.URLError("offline"), io.BytesIO(b"data")],
            ) as request,
            mock.patch.object(godot.time, "sleep"),
        ):
            godot.download("https://example.test/file", path)
            self.assertEqual(request.call_count, 2)
            godot.download("https://example.test/file", path)
            self.assertEqual(request.call_count, 2)
        self.assertEqual(path.read_bytes(), b"data")
        self.assertEqual(list(self.root.glob(".download-*")), [])

    def test_non_network_errors_not_retried_and_no_partial_file(self):
        for error in (
            PermissionError("denied"),
            urllib.error.HTTPError("url", 404, "missing", {}, None),
        ):
            with mock.patch.object(godot.urllib.request, "urlopen", side_effect=error) as request:
                with self.assertRaises(type(error)):
                    godot.download("https://example.test/file", self.root / "download")
                self.assertEqual(request.call_count, 1)
            self.assertFalse((self.root / "download").exists())
            self.assertEqual(list(self.root.glob(".download-*")), [])

    def test_selected_templates_only(self):
        path = self.make_zip(
            [
                ("templates/macos.zip", b"mac"),
                ("templates/windows_release_x86_64.exe", b"win"),
                ("templates/linux_release.x86_64", b"linux"),
            ]
        )
        destination = self.root / "templates"
        godot.install_templates(path, destination, ("macos.zip",))
        self.assertEqual([p.name for p in destination.iterdir()], ["macos.zip"])
        self.assertEqual((destination / "macos.zip").read_bytes(), b"mac")

    def test_editor_extract_and_cache(self):
        name = "Godot_v4.7.2-stable_win64_console.exe"
        path = self.make_zip([(name, b"exe")])
        destination = self.root / "editor"
        with mock.patch.object(godot.sys, "platform", "win32"):
            self.assertEqual(
                godot.install_editor(path, destination, "4.7.2"), (destination / name).resolve()
            )
            with mock.patch.object(zipfile, "ZipFile", side_effect=AssertionError("cached")):
                godot.install_editor(path, destination, "4.7.2")

    def test_mac_uses_ditto(self):
        path = self.make_zip([("Godot.app/Contents/MacOS/Godot", b"exe")])

        def extract(command, check):
            self.assertEqual(command[:3], ["/usr/bin/ditto", "-x", "-k"])
            self.assertTrue(check)
            executable = Path(command[-1]) / "Godot.app/Contents/MacOS/Godot"
            executable.parent.mkdir(parents=True)
            executable.write_bytes(b"exe")

        with (
            mock.patch.object(godot.sys, "platform", "darwin"),
            mock.patch.object(godot.subprocess, "run", side_effect=extract) as run,
        ):
            godot.install_editor(path, self.root / "editor", "4.7.2")
            run.assert_called_once()

    def test_output(self):
        output = self.root / "github-output"
        output.write_text("existing=value\n", encoding="utf-8")
        executable = self.root / "space dir/Godot"
        with (
            mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}),
            contextlib.redirect_stdout(io.StringIO()) as stdout,
        ):
            godot.emit_output(executable)
        self.assertEqual(stdout.getvalue().strip(), str(executable.resolve()))
        self.assertEqual(
            output.read_text(encoding="utf-8"), f"existing=value\ngodot={executable.resolve()}\n"
        )

    def test_main_verifies_both_archives_on_every_run(self):
        editor, _ = godot.platform_config("4.7.2", "win32")
        tpz = "Godot_v4.7.2-stable_export_templates.tpz"
        cache = self.root / "4.7.2-stable"
        cache.mkdir()
        (cache / "SHA512-SUMS.txt").write_text(
            "\n".join(f"{'0' * 128} {name}" for name in (editor, tpz))
        )
        with (
            mock.patch.object(godot.sys, "platform", "win32"),
            mock.patch.object(godot, "download"),
            mock.patch.object(godot, "verify_checksum") as verify,
            mock.patch.object(godot, "install_editor", return_value=self.root / "Godot.exe"),
            mock.patch.object(godot, "install_templates"),
            mock.patch.object(godot, "templates_dir", return_value=self.root / "templates"),
            mock.patch.object(godot, "emit_output"),
        ):
            for _ in range(2):
                godot.main(["--cache-dir", str(self.root)])
            self.assertEqual(
                verify.call_args_list,
                [mock.call(cache / name, "0" * 128) for name in (editor, tpz, editor, tpz)],
            )


if __name__ == "__main__":
    unittest.main()
