"""Refuse files that must never reach the public repository: secrets, local databases and the
itch.io editions of the PIPOYA art (which forbid redistribution; see NOTICE.md).

    python3 tools/check_publish.py          # staged files (pre-commit hook)
    python3 tools/check_publish.py --all    # every tracked file (CI)
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATHS = [
    (re.compile(r"(^|/)\.env$"), "environment file with secrets (.env.example is fine)"),
    (re.compile(r"\.(db|sqlite3?)(\.|$)"), "local database"),
    (re.compile(r"(^|/)data/"), "local data directory"),
    (re.compile(r"(^|/)assets/vendor/"), "vendor assets are not redistributable"),
    (re.compile(r"pipo-popupemotes", re.I), "itch.io Popup Emotes Pack (no redistribution)"),
    (re.compile(r"(^|/)\.godot/"), "Godot cache"),
]
SECRETS = [
    (re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}"), "API key"),
    (re.compile(rb"OPENAI_API_KEY[ \t]*=[ \t]*[^\s#\"']{8,}"), "API key assignment"),
    (re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key"),
]
LICENCE = "anima-pet/assets/pipoya/LICENSE-PIPOYA.md"


def files(all_tracked: bool) -> list[str]:
    cmd = ["git", "ls-files"] if all_tracked else \
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"]
    return [f for f in subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                                      check=True).stdout.splitlines() if f]


def main() -> int:
    all_tracked = "--all" in sys.argv
    problems = []
    names = files(all_tracked)
    for name in names:
        for pattern, why in PATHS:
            if pattern.search(name):
                problems.append(f"{name}: {why}")
        path = ROOT / name
        if path.is_file() and path.stat().st_size < 2_000_000 and not name.endswith(".png"):
            data = path.read_bytes()
            for pattern, why in SECRETS:
                if pattern.search(data):
                    problems.append(f"{name}: looks like it contains a {why}")
    if any(n.startswith("anima-pet/assets/pipoya/") for n in names) and \
            not (ROOT / LICENCE).exists():
        problems.append(f"PIPOYA assets without {LICENCE}")
    for p in problems:
        print("check_publish:", p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
