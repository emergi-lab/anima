"""Packaging boundary tests; no downloads or real user data."""
from __future__ import annotations

import importlib.util
import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "build_desktop", Path(__file__).resolve().parents[1] / "build_desktop.py"
)
assert SPEC and SPEC.loader
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


class BuildTests(unittest.TestCase):
    def test_staging_allowlist_removes_editor_and_local_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = {
                "project.godot": '[application]\nconfig/name="Anima"\n[autoload]\n'
                                 'App="*res://scripts/app.gd"\n'
                                 '_mcp_game_helper="*res://addons/godot_ai/runtime/game_helper.gd"\n'
                                 '[editor_plugins]\nenabled=PackedStringArray("plugin")\n'
                                 '[rendering]\nrenderer/rendering_method="gl_compatibility"\n',
                "export_presets.cfg": "presets",
                "scripts/app.gd": "extends Node",
                "assets/pipoya/manifest.json": "{}",
                "assets/fonts/test.ttf": "font",
                "addons/plugin.gd": "editor-only",
                "tests/test.gd": "test-only",
                ".godot/cache": "local-cache",
                ".env": "private",
                "data/model.json": "private",
            }
            for name, text in files.items():
                source = root / "anima-pet" / name
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text(text, encoding="utf-8")
            listing = "\0".join("anima-pet/" + name for name in files).encode()
            destination = root / "stage"
            with patch.object(build, "ROOT", root), patch.object(
                build.subprocess, "check_output", return_value=listing
            ):
                build.stage_project(destination)
            text = (destination / "project.godot").read_text(encoding="utf-8")
            self.assertIn('App="*res://scripts/app.gd"', text)
            self.assertIn("[rendering]", text)
            self.assertNotIn("_mcp_game_helper", text)
            self.assertNotIn("[editor_plugins]", text)
            self.assertTrue((destination / "assets/pipoya/manifest.json").exists())
            for name in ("addons", "tests", ".godot", ".env", "data"):
                self.assertFalse((destination / name).exists())

    def test_macos_bundle_layout_matches_launcher(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            frozen = root / "frozen"
            frozen.mkdir()
            (frozen / "Anima").write_text("executable")
            (frozen / "_internal").mkdir()
            launcher, resources = build.make_macos_app(frozen, root / "output", "1.2.3")
            self.assertEqual(launcher.name, "Anima")
            self.assertTrue(launcher.is_file())
            self.assertEqual(resources.name, "Resources")
            with (resources.parent / "Info.plist").open("rb") as stream:
                info = plistlib.load(stream)
            self.assertEqual(info["CFBundleExecutable"], launcher.name)
            self.assertEqual(info["CFBundleShortVersionString"], "1.2.3")
            self.assertTrue(info["LSUIElement"])


if __name__ == "__main__":
    unittest.main()
