"""Installer packaging boundaries; no real installs, downloads or user data."""
from __future__ import annotations

import hashlib
import importlib.util
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "build_installers", Path(__file__).resolve().parents[1] / "build_installers.py"
)
assert SPEC and SPEC.loader
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.name = "Anima-0.0.1-windows-x64"
        self.archive = self.root / (self.name + ".zip")

    def zip(self, files=None, links=None):
        with zipfile.ZipFile(self.archive, "w") as stream:
            for name, text in (files or {}).items():
                info = zipfile.ZipInfo(name)
                # Keep foreign ZIP names verbatim; Windows ZipInfo normalizes backslashes.
                info.filename = name
                stream.writestr(info, text)
            for name, target in (links or {}).items():
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                stream.writestr(info, target)

    def test_checksum_checks_content_and_filename(self):
        self.zip({self.name + "/README.md": "test"})
        checksum = self.root / "archive.sha256"
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        checksum.write_text(f"{digest}  {self.archive.name}\n", encoding="ascii")
        build.verify_archive(self.archive, checksum)
        checksum.write_text(f"{digest}  another.zip\n", encoding="ascii")
        with self.assertRaises(ValueError):
            build.verify_archive(self.archive, checksum)
        checksum.write_text(f"{'0' * 64}  {self.archive.name}\n", encoding="ascii")
        with self.assertRaises(ValueError):
            build.verify_archive(self.archive, checksum)

    def test_normal_payload_and_apple_metadata(self):
        self.zip({self.name + "/Anima/Anima.exe": "app", "__MACOSX/._README.md": "meta"})
        build.validate_members(self.archive, self.name, macos=False)

    def test_rejects_path_traversal_and_other_package(self):
        for name in ("../escape", "/absolute", "C:/escape", self.name + "/../escape",
                     self.name + "\\escape", "Another/Anima.exe", self.name + "/file:stream"):
            with self.subTest(name=name):
                self.zip({name: "invalid"})
                with self.assertRaises(ValueError):
                    build.validate_members(self.archive, self.name, macos=True)

    def test_rejects_duplicate_members(self):
        with zipfile.ZipFile(self.archive, "w") as stream:
            stream.writestr(self.name + "/same", "1")
            with self.assertWarns(UserWarning):
                stream.writestr(self.name + "/same", "2")
        with self.assertRaises(ValueError):
            build.validate_members(self.archive, self.name, macos=False)

    def test_preserves_internal_macos_links_but_not_windows_links(self):
        self.zip({self.name + "/app/Resources/library": "lib"},
                 {self.name + "/app/Frameworks/library": "../Resources/library"})
        build.validate_members(self.archive, self.name, macos=True)
        with self.assertRaises(ValueError):
            build.validate_members(self.archive, self.name, macos=False)

    def test_rejects_link_escape_cycle_and_link_parent(self):
        for links in ({self.name + "/a": "/tmp"},
                      {self.name + "/a": "../escape"},
                      {self.name + "/a": "b", self.name + "/b": "a"},
                      {self.name + "/sub/a": "..", self.name + "/sub/b": "a/../escape"}):
            with self.subTest(links=links):
                self.zip(links=links)
                with self.assertRaises(ValueError):
                    build.validate_members(self.archive, self.name, macos=True)
        self.zip({self.name + "/a/file": "write"}, {self.name + "/a": "other"})
        with self.assertRaises(ValueError):
            build.validate_members(self.archive, self.name, macos=True)

    def test_windows_extract_and_metadata_verification(self):
        self.zip({self.name + "/Anima/Anima.exe": "launcher",
                  self.name + "/Anima/client/Anima.exe": "client",
                  self.name + "/Anima/build-info.txt":
                  "Anima 0.0.1\nTarget: windows-x64\nCommit: release-commit\n"})
        checksum = self.root / "archive.sha256"
        checksum.write_text(f"{build.sha256(self.archive)}  {self.archive.name}\n")
        package = build.extract_package(self.archive, checksum, self.root / "extract",
                                        self.name, macos=False)
        self.assertEqual(build.validate_package(package, "windows-x64", "0.0.1"),
                         package / "Anima")
        with self.assertRaises(ValueError):
            build.validate_package(package, "windows-x64", "0.0.2")
        (package / "Anima/client/Anima.exe").unlink()
        with self.assertRaises(ValueError):
            build.validate_package(package, "windows-x64", "0.0.1")

    def test_directory_digest_detects_changes(self):
        (self.root / "file").write_bytes(b"before")
        before = build.tree_digest(self.root)
        (self.root / "file").write_bytes(b"after")
        self.assertNotEqual(before, build.tree_digest(self.root))


if __name__ == "__main__":
    unittest.main()
