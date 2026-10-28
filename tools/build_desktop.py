"""Build a self-contained desktop distribution on its native OS (no user data copied).

    uv run --project anima-server --locked --with pyinstaller==6.20.0 \
        python tools/build_desktop.py --godot /path/to/godot --target windows-x64
"""
from __future__ import annotations

import argparse
import hashlib
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GODOT_VERSION = "4.7.2"
TARGETS = {
    "windows-x64": ("win32", "x86_64"),
    "macos-arm64": ("darwin", "arm64"),
    "macos-x64": ("darwin", "x86_64"),
}


def run(*args: str | Path, cwd: Path = ROOT, timeout: int = 1200) -> None:
    print("+", " ".join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), cwd=cwd, check=True, timeout=timeout)


def stage_project(destination: Path) -> None:
    """Allowlist tracked runtime files; never copy caches, editor plugins or local data."""
    names = subprocess.check_output(
        ["git", "ls-files", "-z", "anima-pet"], cwd=ROOT
    ).decode("utf-8").split("\0")
    allowed_dirs = {"assets", "locales", "scenes", "scripts"}
    allowed_files = {"project.godot", "icon.svg", "icon.svg.import", "export_presets.cfg"}
    for name in filter(None, names):
        relative = Path(name).relative_to("anima-pet")
        if relative.parts[0] not in allowed_dirs and relative.as_posix() not in allowed_files:
            continue
        source = ROOT / name
        if source.is_symlink():
            raise ValueError(f"Runtime symlinks are not supported: {name}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    # Presets can be newly added while testing a local, not-yet-committed change.
    shutil.copy2(ROOT / "anima-pet/export_presets.cfg", destination / "export_presets.cfg")
    project = destination / "project.godot"
    text = project.read_text(encoding="utf-8")
    text = re.sub(r'^_mcp_game_helper=.*\n', "", text, flags=re.MULTILINE)
    text = re.sub(r'\[editor_plugins\]\s*.*?(?=\n\[|\Z)', "", text, flags=re.DOTALL)
    project.write_text(text, encoding="utf-8")


def export_client(godot: Path, project: Path, destination: Path, target: str) -> Path:
    run(godot, "--headless", "--path", project, "--editor", "--import")
    if target == "windows-x64":
        destination.mkdir(parents=True)
        client = destination / "Anima.exe"
        run(godot, "--headless", "--path", project, "--export-release", "Windows Desktop", client)
        return client
    # Official macos.zip contains universal binaries, not per-architecture templates.
    # Export universal first, then keep only the runner's native slice before signing.
    destination.mkdir(parents=True)
    app = destination / "AnimaClient.app"
    run(godot, "--headless", "--path", project, "--export-release", "macOS", app)
    with (app / "Contents/Info.plist").open("rb") as stream:
        executable = plistlib.load(stream)["CFBundleExecutable"]
    # The launcher uses this fixed path; do not silently ship a broken bundle if Godot changes it.
    if executable != "Anima":
        raise RuntimeError(f"Unexpected Godot CFBundleExecutable: {executable}")
    binary = app / "Contents/MacOS" / executable
    thin = binary.with_suffix(".thin")
    run("lipo", binary, "-thin", TARGETS[target][1], "-output", thin)
    thin.replace(binary)
    binary.chmod(0o755)
    return binary


def add_licenses(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for relative in (
        "LICENSE", "NOTICE.md", "anima-pet/assets/pipoya/LICENSE-PIPOYA.md",
        "anima-pet/assets/fonts/FusionPixel-OFL.txt",
        "anima-pet/assets/fonts/NotoSansSC-OFL.txt",
    ):
        shutil.copy2(ROOT / relative, destination / Path(relative).name)
    for filename in ("LICENSE.txt", "COPYRIGHT.txt"):
        url = f"https://raw.githubusercontent.com/godotengine/godot/{GODOT_VERSION}-stable/{filename}"
        with urllib.request.urlopen(url, timeout=60) as response:
            (destination / f"Godot-{filename}").write_bytes(response.read())
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        shutil.copy2(python_license, destination / "Python-LICENSE.txt")
    else:
        url = ("https://raw.githubusercontent.com/python/cpython/"
               f"v{platform.python_version()}/LICENSE")
        with urllib.request.urlopen(url, timeout=60) as response:
            (destination / "Python-LICENSE.txt").write_bytes(response.read())
    # Include installed Python distribution license texts, not just package names.
    import importlib.metadata
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name", "unknown")
        for file in distribution.files or []:
            if any(part.lower().startswith(("license", "copying", "notice"))
                   for part in file.parts):
                source = Path(distribution.locate_file(file))
                if source.is_file():
                    relative = Path(*(part for part in file.parts if part not in ("..", ".")))
                    dest = destination / "python" / name / relative
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, dest)


def make_macos_app(frozen: Path, output: Path, version: str) -> tuple[Path, Path]:
    app = output / "Anima.app"
    shutil.copytree(frozen, app, symlinks=True)
    contents = app / "Contents"
    resources = contents / "Resources"
    resources.mkdir(exist_ok=True)
    with (contents / "Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    info.update({
        "CFBundleName": "Anima", "CFBundleDisplayName": "Anima",
        "CFBundleIdentifier": "org.emergi.anima", "CFBundleExecutable": "Anima",
        "CFBundlePackageType": "APPL", "CFBundleShortVersionString": version,
        "CFBundleVersion": version, "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True, "LSUIElement": True,
    })
    with (contents / "Info.plist").open("wb") as stream:
        plistlib.dump(info, stream)
    return contents / "MacOS/Anima", resources


def smoke_launcher(launcher: Path, logs: Path) -> None:
    # Windowed Windows executables have no stderr; retain disposable diagnostics explicitly.
    env = os.environ | {"ANIMA_SMOKE_LOG_DIR": str(logs)}
    result = subprocess.run([str(launcher), "--smoke-test"], env=env,
                            timeout=180, check=False)
    if result.returncode:
        for log in logs.glob("*.log"):
            print(log.read_text(encoding="utf-8", errors="replace"), flush=True)
        raise RuntimeError(f"Frozen backend smoke failed: {result.returncode}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--godot", type=Path, required=True)
    parser.add_argument("--target", choices=TARGETS, required=True)
    parser.add_argument("--version", default="0.1.0")
    args = parser.parse_args()
    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        parser.error("--version must be MAJOR.MINOR.PATCH")
    system, arch = TARGETS[args.target]
    machine = platform.machine().lower().replace("amd64", "x86_64").replace("aarch64", "arm64")
    if sys.platform != system or machine != arch:
        parser.error(f"{args.target} needs native {system}/{arch} Python; "
                     f"got {sys.platform}/{machine}")
    if (Path(sys.base_prefix) / "conda-meta").exists():
        parser.error("Use standalone CPython (uv managed or python.org), not an Anaconda venv")
    godot = args.godot.resolve()
    reported = subprocess.check_output([str(godot), "--version"], text=True).strip()
    if not reported.startswith(f"{GODOT_VERSION}.stable."):
        parser.error(f"Godot {GODOT_VERSION}.stable required, got {reported}")
    work = ROOT / ".build" / args.target
    if work.exists():
        shutil.rmtree(work)
    project = work / "project"
    stage_project(project)
    run(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", work / "frozen", "--workpath", work / "pyinstaller",
        ROOT / "tools/anima.spec")
    frozen = work / "frozen/Anima"
    package_name = f"Anima-{args.version}-{args.target}"
    package = work / package_name
    package.mkdir()
    if system == "win32":
        application = package / "Anima"
        shutil.copytree(frozen, application)
        launcher, resources = application / "Anima.exe", application
    else:
        launcher, resources = make_macos_app(work / "frozen/Anima.app", package, args.version)
    client = export_client(godot, project, resources / "client", args.target)
    add_licenses(resources / "licenses")
    shutil.copy2(ROOT / "docs/desktop-download.md", package / "README.md")
    (resources / "build-info.txt").write_text(
        f"Anima {args.version}\nTarget: {args.target}\nGodot: {reported}\nCommit: "
        + subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip() + "\n",
        encoding="utf-8",
    )
    if system == "darwin":
        # Ad-hoc signing is required on Apple Silicon. This is NOT Developer ID/notarization.
        run("codesign", "--force", "--deep", "--sign", "-", package / "Anima.app")
        run("codesign", "--verify", "--deep", "--strict", package / "Anima.app")
    smoke_launcher(launcher, work / "smoke-logs")
    # Headless client test checks exported scripts/assets, not actual desktop composition.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="anima-client-smoke-") as temporary:
        env = os.environ.copy()
        env.update(APPDATA=temporary, LOCALAPPDATA=temporary, HOME=temporary,
                   XDG_DATA_HOME=temporary, ANIMA_API="http://127.0.0.1:1",
                   ANIMA_TOKEN="smoke-test")
        result = subprocess.run([str(client), "--headless", "--quit-after", "10"],
                                env=env, capture_output=True, text=True, timeout=120, check=False)
        print(result.stdout, flush=True)
        print(result.stderr, flush=True)
        if result.returncode or "SCRIPT ERROR" in result.stderr or "ERROR:" in result.stderr:
            raise RuntimeError("Exported Godot client smoke test failed")
        # Also verify the real entry point launches both processes with the packaged paths.
        session_data = Path(temporary) / "session"
        env["ANIMA_DATA_DIR"] = str(session_data)
        result = subprocess.run([str(launcher), "--headless", "--quit-after", "120"],
                                env=env, timeout=180, check=False)
        failed = result.returncode != 0
        for log in (session_data / "logs").glob("*.log"):
            text = log.read_text(encoding="utf-8", errors="replace")
            if "SCRIPT ERROR" in text or "ERROR:" in text:
                failed = True
            if failed:
                print(text, flush=True)
        if failed:
            raise RuntimeError("Packaged launcher/client integration smoke test failed")
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    archive = output / f"{package_name}.zip"
    if system == "darwin":
        run("ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", package, archive)
    else:
        shutil.make_archive(str(archive.with_suffix("")), "zip",
                            root_dir=work, base_dir=package_name)
    with archive.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{checksum}  {archive.name}\n", encoding="ascii")
    print(f"Built: {archive}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
