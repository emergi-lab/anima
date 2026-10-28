"""Wrap a tested desktop payload in a native Windows setup EXE or macOS DMG.

--package accepts build_desktop.py's staged package. --archive + --checksum wraps
an existing, checksum-verified release ZIP without rebuilding its application.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import platform
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {"windows-x64": ("win32", "x86_64"),
           "macos-arm64": ("darwin", "arm64"), "macos-x64": ("darwin", "x86_64")}


def run(*args: str | Path, **kwargs) -> None:
    print("+", " ".join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), check=True, timeout=600, **kwargs)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_archive(archive: Path, checksum: Path) -> None:
    fields = checksum.read_text(encoding="ascii").strip().split()
    if (len(fields) != 2 or not re.fullmatch(r"[0-9a-f]{64}", fields[0])
            or fields[1] != archive.name or sha256(archive) != fields[0]):
        raise ValueError("Archive SHA-256 / filename mismatch")


def validate_members(archive: Path, package_name: str, *, macos: bool) -> None:
    """Validate paths and internal bundle symlinks before ditto or zipfile writes."""
    with zipfile.ZipFile(archive) as source:
        names: set[str] = set()
        links: dict[str, str] = {}
        for item in source.infolist():
            original = item.orig_filename
            path = PurePosixPath(original)
            if (not path.parts or path.is_absolute() or ".." in path.parts
                    or "\\" in original or ":" in original or "\x00" in original
                    or path.parts[0] not in (package_name, "__MACOSX")):
                raise ValueError(f"Unsafe ZIP path: {original}")
            name = path.as_posix()
            if name in names:
                raise ValueError(f"Duplicate ZIP path: {name}")
            names.add(name)
            if stat.S_ISLNK(item.external_attr >> 16):
                if not macos:
                    raise ValueError("Windows payload must not contain symlinks")
                target = source.read(item).decode("utf-8")
                if not target or target.startswith("/") or "\\" in target or ":" in target:
                    raise ValueError(f"Unsafe ZIP symlink: {name}")
                links[name] = target
        for name in names:
            if any(parent.as_posix() in links for parent in PurePosixPath(name).parents):
                raise ValueError(f"ZIP member nested below symlink: {name}")
        for name, target in links.items():
            resolved = list(PurePosixPath(name).parent.parts)
            pending = target.split("/")
            hops = 0
            while pending:
                part = pending.pop(0)
                if part in ("", "."):
                    continue
                if part == "..":
                    if len(resolved) <= 1:
                        raise ValueError(f"ZIP symlink escapes package: {name}")
                    resolved.pop()
                    continue
                candidate = "/".join([*resolved, part])
                if candidate in links:
                    hops += 1
                    if hops > 40:
                        raise ValueError(f"ZIP symlink cycle: {name}")
                    pending = links[candidate].split("/") + pending
                else:
                    resolved.append(part)


def extract_package(archive: Path, checksum: Path, destination: Path,
                    package_name: str, *, macos: bool) -> Path:
    verify_archive(archive, checksum)
    validate_members(archive, package_name, macos=macos)
    destination.mkdir()
    if macos:
        run("ditto", "-x", "-k", archive, destination)
    else:
        with zipfile.ZipFile(archive) as source:
            source.extractall(destination)
    return destination / package_name


def validate_package(package: Path, target: str, version: str) -> Path:
    macos = target.startswith("macos-")
    app = package / ("Anima.app" if macos else "Anima")
    resources = app / "Contents/Resources" if macos else app
    required = (["Contents/MacOS/Anima", "Contents/Info.plist",
                 "Contents/Resources/client/AnimaClient.app/Contents/MacOS/Anima"]
                if macos else ["Anima.exe", "client/Anima.exe"])
    if not all((app / name).is_file() for name in required):
        raise ValueError("Incomplete application payload")
    info = (resources / "build-info.txt").read_text(encoding="utf-8")
    if not info.startswith(f"Anima {version}\nTarget: {target}\n"):
        raise ValueError("Payload version / target mismatch")
    if macos:
        with (app / "Contents/Info.plist").open("rb") as stream:
            if plistlib.load(stream)["CFBundleShortVersionString"] != version:
                raise ValueError("App bundle version mismatch")
    return app


def tree_digest(directory: Path) -> dict[str, str]:
    return {path.relative_to(directory).as_posix():
            "link:" + os.readlink(path) if path.is_symlink() else sha256(path)
            for path in directory.rglob("*") if path.is_file() or path.is_symlink()}


def smoke_macos(app: Path, work: Path) -> None:
    launcher = app / "Contents/MacOS/Anima"
    with tempfile.TemporaryDirectory(prefix="anima-install-smoke-") as temporary:
        env = os.environ | {"HOME": temporary, "APPDATA": temporary,
                            "LOCALAPPDATA": temporary, "XDG_DATA_HOME": temporary,
                            "ANIMA_DATA_DIR": str(Path(temporary) / "session"),
                            "ANIMA_SMOKE_LOG_DIR": str(work / "smoke-logs")}
        run(launcher, "--smoke-test", env=env)
        run(launcher, "--headless", "--quit-after", "120", env=env)
        logs = Path(temporary) / "session/logs"
        for log in logs.glob("*.log"):
            text = log.read_text(encoding="utf-8", errors="replace")
            if "SCRIPT ERROR" in text or "ERROR:" in text:
                raise RuntimeError(f"Installed application smoke failed: {log.name}\n{text}")


def build_dmg(app: Path, output: Path, work: Path, version: str) -> None:
    run("codesign", "--verify", "--deep", "--strict", app)
    stage = work / "dmg-stage"
    stage.mkdir()
    run("ditto", app, stage / "Anima.app")
    (stage / "Applications").symlink_to("/Applications")
    shutil.copy2(ROOT / "docs/desktop-download.md", stage / "README.md")
    run("hdiutil", "create", "-volname", f"Anima {version}", "-srcfolder", stage,
        "-format", "UDZO", "-fs", "HFS+", output)
    run("hdiutil", "verify", output)
    mount = work / "mounted"
    mount.mkdir()
    run("hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", mount, output)
    installed = work / "install-test/Applications/Anima.app"
    try:
        if os.readlink(mount / "Applications") != "/Applications":
            raise RuntimeError("DMG is missing the Applications installation shortcut")
        run("ditto", mount / "Anima.app", installed)
    finally:
        run("hdiutil", "detach", mount)
    if tree_digest(app) != tree_digest(installed):
        raise RuntimeError("DMG changed the original application payload")
    run("codesign", "--verify", "--deep", "--strict", installed)
    smoke_macos(installed, work)


def build_windows(app: Path, output: Path, version: str) -> None:
    compiler = (os.environ.get("MAKENSIS") or shutil.which("makensis")
                or str(Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"))
                       / "NSIS/makensis.exe"))
    if not Path(compiler).is_file():
        raise RuntimeError("NSIS is required: install it or set MAKENSIS to makensis.exe")
    run(compiler, "/V3", "/INPUTCHARSET", "UTF8", f"/DVERSION={version}",
        f"/DPAYLOAD={app}", f"/DOUTPUT={output}", ROOT / "tools/installers/windows.nsi")
    run("pwsh", "-NoProfile", "-File", ROOT / "tools/installers/test_windows_install.ps1",
        "-Installer", output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=TARGETS, required=True)
    parser.add_argument("--version", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--package", type=Path)
    source.add_argument("--archive", type=Path)
    parser.add_argument("--checksum", type=Path)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        parser.error("Version must be MAJOR.MINOR.PATCH")
    machine = platform.machine().lower().replace("amd64", "x86_64").replace("aarch64", "arm64")
    if (sys.platform, machine) != TARGETS[args.target]:
        parser.error("Installers must be built and tested on their native OS / architecture")
    if args.archive and not args.checksum:
        parser.error("--archive requires --checksum")
    package_name = f"Anima-{args.version}-{args.target}"
    output = args.output_dir.resolve() / (package_name + (
        "-setup.exe" if args.target == "windows-x64" else ".dmg"))
    if output.exists():
        parser.error(f"Refusing to overwrite existing installer: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    (ROOT / ".build").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f"installer-{args.target}-", dir=ROOT / ".build"
    ) as tmp:
        work = Path(tmp)
        package = (extract_package(args.archive.resolve(), args.checksum.resolve(), work / "input",
                                   package_name, macos=sys.platform == "darwin")
                   if args.archive else args.package.resolve())
        app = validate_package(package, args.target, args.version)
        if sys.platform == "win32":
            build_windows(app, output, args.version)
        else:
            build_dmg(app, output, work, args.version)
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{sha256(output)}  {output.name}\n", encoding="ascii")
    print(f"Built and installation-tested: {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
