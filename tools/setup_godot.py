#!/usr/bin/env python3
"""Install the official Godot editor and platform export templates (stdlib only)."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

DEFAULT_VERSION = "4.7.2"
RELEASE_URL = "https://github.com/godotengine/godot-builds/releases/download"


def platform_config(version: str, platform: str | None = None):
    platform = sys.platform if platform is None else platform
    if platform == "win32":
        return f"Godot_v{version}-stable_win64.exe.zip", (
            "windows_release_x86_64.exe",
            "windows_debug_x86_64.exe",
        )
    if platform == "darwin":
        return f"Godot_v{version}-stable_macos.universal.zip", ("macos.zip",)
    raise ValueError(f"Unsupported platform: {platform}; Windows and macOS only")


def templates_dir(version: str, platform: str | None = None) -> Path:
    platform = sys.platform if platform is None else platform
    if platform == "win32":
        root = Path(os.environ["APPDATA"]) / "Godot"
    elif platform == "darwin":
        root = Path.home() / "Library" / "Application Support" / "Godot"
    else:
        raise ValueError(f"Unsupported platform: {platform}")
    return root / "export_templates" / f"{version}.stable"


def parse_checksums(text: str) -> dict[str, str]:
    result = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"([0-9a-fA-F]{128})[ \t]+\*?(.+)", line)
        if not match:
            raise ValueError(f"Malformed SHA512-SUMS line: {line!r}")
        digest, name = match.groups()
        if name in result:
            raise ValueError(f"Duplicate checksum: {name}")
        result[name] = digest.lower()
    return result


def verify_checksum(path: Path, expected: str) -> None:
    digest = hashlib.sha512()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected.lower():
        raise ValueError(f"SHA512 mismatch: {path}; remove this cached file and retry")


def download(url: str, destination: Path, attempts: int = 3) -> None:
    """Cache downloads, publishing only complete responses. Retry network failures only."""
    if destination.is_file():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(attempts):
        fd, temporary = tempfile.mkstemp(prefix=".download-", dir=destination.parent)
        try:
            with os.fdopen(fd, "wb") as output:
                request = urllib.request.Request(url, headers={"User-Agent": "anima-godot-setup"})
                with urllib.request.urlopen(request, timeout=120) as response:
                    shutil.copyfileobj(response, output, 1024 * 1024)
            os.replace(temporary, destination)
            return
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code not in (
                408,
                429,
                500,
                502,
                503,
                504,
            ):
                raise
            if attempt + 1 == attempts:
                raise
            time.sleep(2**attempt)
        finally:
            Path(temporary).unlink(missing_ok=True)


def safe_zip_path(name: str) -> tuple[str, ...]:
    # Backslashes and colons also have special meaning on Windows.
    if not name or name.startswith("/") or "\\" in name or ":" in name or "\x00" in name:
        raise ValueError(f"Unsafe ZIP path: {name!r}")
    parts = PurePosixPath(name).parts
    if not parts or any(part in ("..", ".") for part in parts):
        raise ValueError(f"Unsafe ZIP path: {name!r}")
    if any(
        part.endswith((".", " "))
        or part.split(".")[0].upper()
        in {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10)),
        }
        for part in parts
    ):
        raise ValueError(f"Unsafe ZIP path: {name!r}")
    return parts


def validate_zip(archive: zipfile.ZipFile) -> None:
    """Validate even unused entries before extraction; allow only contained symlinks."""
    links = {}
    seen = set()
    for info in archive.infolist():
        parts = safe_zip_path(info.filename)
        key = tuple(part.casefold() for part in parts)
        if key in seen:
            raise ValueError(f"Duplicate ZIP path: {info.filename}")
        seen.add(key)
        mode = info.external_attr >> 16
        if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK):
            raise ValueError(f"Unsupported ZIP entry: {info.filename}")
        if stat.S_ISLNK(mode):
            target = archive.read(info).decode("utf-8")
            if (
                not target
                or target.startswith("/")
                or any(c in target for c in ("\\", ":", "\x00"))
            ):
                raise ValueError(f"Unsafe symlink: {info.filename}")
            links[key] = target.split("/")
    for info in archive.infolist():
        parts = safe_zip_path(info.filename)
        # Do not write through a symlink, regardless of archive entry order.
        if any(tuple(p.casefold() for p in parts[:i]) in links for i in range(1, len(parts))):
            raise ValueError(f"ZIP entry beneath symlink: {info.filename}")
    for link, target in links.items():
        resolved = list(link[:-1])
        pending = list(target)
        hops = 0
        while pending:
            part = pending.pop(0)
            if part in ("", "."):
                continue
            if part == "..":
                if not resolved:
                    raise ValueError("ZIP symlink escapes extraction directory")
                resolved.pop()
                continue
            safe_zip_path(part)
            resolved.append(part.casefold())
            nested = links.get(tuple(resolved))
            if nested is not None:
                hops += 1
                if hops > 40:
                    raise ValueError("ZIP symlink cycle or excessive depth")
                resolved.pop()
                pending = nested + pending


def editor_executable(root: Path, version: str, platform: str | None = None) -> Path:
    platform = sys.platform if platform is None else platform
    if platform == "darwin":
        candidates = [root / "Godot.app" / "Contents" / "MacOS" / "Godot"]
    elif platform == "win32":
        candidates = [
            root / f"Godot_v{version}-stable_win64{suffix}.exe" for suffix in ("_console", "")
        ]
    else:
        raise ValueError(f"Unsupported platform: {platform}")
    for path in candidates:
        if path.is_file():
            return path.resolve()
    raise FileNotFoundError(f"Godot executable not found in {root}")


def install_editor(archive_path: Path, destination: Path, version: str) -> Path:
    if destination.is_dir():
        return editor_executable(destination, version)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".editor-", dir=destination.parent) as temporary:
        root = Path(temporary) / "editor"
        root.mkdir()
        with zipfile.ZipFile(archive_path) as archive:
            validate_zip(archive)
            if sys.platform == "darwin":
                # ditto preserves app executable permissions and framework symlinks.
                subprocess.run(
                    ["/usr/bin/ditto", "-x", "-k", str(archive_path.resolve()), str(root)],
                    check=True,
                )
            else:
                if any(stat.S_ISLNK(i.external_attr >> 16) for i in archive.infolist()):
                    raise ValueError("Unexpected symlink in Windows editor archive")
                archive.extractall(root)
        editor_executable(root, version)
        root.rename(destination)
    return editor_executable(destination, version)


def install_templates(archive_path: Path, destination: Path, names: tuple[str, ...]) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        validate_zip(archive)
        entries = [archive.getinfo(f"templates/{name}") for name in names]
        if any(i.is_dir() or stat.S_ISLNK(i.external_attr >> 16) for i in entries):
            raise ValueError("Expected regular export template files")
        destination.mkdir(parents=True, exist_ok=True)
        for name, info in zip(names, entries, strict=True):
            fd, temporary = tempfile.mkstemp(prefix=".template-", dir=destination)
            try:
                with os.fdopen(fd, "wb") as output, archive.open(info) as source:
                    shutil.copyfileobj(source, output, 1024 * 1024)
                os.replace(temporary, destination / name)
            finally:
                Path(temporary).unlink(missing_ok=True)


def emit_output(executable: Path) -> None:
    value = str(executable.resolve())
    if "\n" in value or "\r" in value:
        raise ValueError("Executable path cannot contain newlines")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write(f"godot={value}\n")
    print(value)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--cache-dir", type=Path, default=Path(".build/godot"))
    args = parser.parse_args(argv)
    if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", args.version):
        parser.error("--version must be a stable numeric version (for example 4.7.2)")
    editor_name, templates = platform_config(args.version)
    cache = args.cache_dir.resolve() / f"{args.version}-stable"
    url = f"{RELEASE_URL}/{args.version}-stable"
    sums = cache / "SHA512-SUMS.txt"
    download(f"{url}/{sums.name}", sums)
    checksums = parse_checksums(sums.read_text(encoding="utf-8"))
    template_name = f"Godot_v{args.version}-stable_export_templates.tpz"
    for name in (editor_name, template_name):
        if name not in checksums:
            raise ValueError(f"Official SHA512 missing for {name}")
        download(f"{url}/{name}", cache / name)
        verify_checksum(cache / name, checksums[name])
    executable = install_editor(cache / editor_name, cache / f"editor-{sys.platform}", args.version)
    install_templates(cache / template_name, templates_dir(args.version), templates)
    emit_output(executable)


if __name__ == "__main__":
    main()
