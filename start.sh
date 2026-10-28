#!/bin/bash
# macOS launcher; compatible with the system Bash 3.2. Run from any directory.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
GODOT_PATH="${GODOT_BIN:-}"
SERVER_ONLY=0

usage() {
    cat <<'EOF'
Usage: bash start.sh [--godot-path PATH] [--server-only]

  --godot-path PATH  Godot executable, Godot.app, or its containing folder.
                    Defaults to GODOT_BIN, PATH, then common app locations.
  --server-only     Start/check the backend without requiring Godot.
  -h, --help        Show this help.

Windows-style -GodotPath and -ServerOnly are also accepted.
EOF
}

fail() { printf '[Anima] ERROR: %s\n' "$*" >&2; exit 1; }

while [ "$#" -gt 0 ]; do
    case "$1" in
        --godot-path|-GodotPath)
            [ "$#" -ge 2 ] && [ -n "$2" ] || fail 'Missing value for --godot-path.'
            GODOT_PATH="$2"; shift 2 ;;
        --server-only|-ServerOnly) SERVER_ONLY=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) fail "Unknown option: $1 (use --help)." ;;
    esac
done

[ "$(uname -s)" = Darwin ] || fail 'Use start.bat on Windows; start.sh is for macOS.'
# Finder's Terminal session may not include the usual uv/Homebrew locations.
export PATH="${PATH}:/opt/homebrew/bin:/usr/local/bin:${HOME}/.local/bin:${HOME}/.cargo/bin"

resolve_candidate() {
    local candidate="$1" executable
    if [ -d "$candidate" ]; then
        for executable in "$candidate/Contents/MacOS/Godot" \
            "$candidate"/Godot*.app/Contents/MacOS/Godot "$candidate/Godot" "$candidate/godot"; do
            if [ -f "$executable" ] && [ -x "$executable" ]; then
                printf '%s/%s\n' "$(CDPATH= cd -- "$(dirname -- "$executable")" && pwd -P)" "$(basename -- "$executable")"
                return 0
            fi
        done
    elif [ -f "$candidate" ] && [ -x "$candidate" ]; then
        printf '%s/%s\n' "$(CDPATH= cd -- "$(dirname -- "$candidate")" && pwd -P)" "$(basename -- "$candidate")"
        return 0
    elif executable="$(command -v "$candidate" 2>/dev/null)" && [ -f "$executable" ] && [ -x "$executable" ]; then
        printf '%s/%s\n' "$(CDPATH= cd -- "$(dirname -- "$executable")" && pwd -P)" "$(basename -- "$executable")"
        return 0
    fi
    return 1
}

resolve_godot() {
    local candidate
    if [ -n "$GODOT_PATH" ]; then
        resolve_candidate "$GODOT_PATH" || fail "Godot executable not found: $GODOT_PATH"
        return
    fi
    for candidate in godot godot4 /Applications/Godot.app "$HOME/Applications/Godot.app" "$ROOT" "$ROOT/tools"; do
        if resolve_candidate "$candidate"; then return 0; fi
    done
    fail 'Godot not found. Use --godot-path "/Applications/Godot.app" or set GODOT_BIN.'
}

UV="$(command -v uv 2>/dev/null)" || fail 'uv not found. Install uv, then reopen this launcher.'
if [ "$SERVER_ONLY" -eq 0 ]; then GODOT_PATH="$(resolve_godot)"; fi
cd -- "$ROOT/anima-server"
if [ ! -e .env ]; then cp -- .env.example .env; fi
printf '[Anima] Preparing Python dependencies...\n'
"$UV" sync --locked || fail 'Dependency installation failed. Check the output above.'
PYTHON="$ROOT/anima-server/.venv/bin/python"
[ -x "$PYTHON" ] || fail 'Virtual environment is missing .venv/bin/python. Recreate it on this Mac with uv sync --locked.'
if [ "$SERVER_ONLY" -eq 1 ]; then
    exec "$PYTHON" "$ROOT/tools/start_macos.py" --server-only
else
    exec "$PYTHON" "$ROOT/tools/start_macos.py" --godot-path "$GODOT_PATH"
fi
