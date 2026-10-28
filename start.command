#!/bin/bash
# Optional Finder entry point. Enable once with: chmod +x start.command
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)" || exit 1
/bin/bash "$ROOT/start.sh" "$@"
result=$?
if [ "$result" -ne 0 ] && [ -t 0 ]; then
    printf '\n[Anima] Press Return to close this window.'
    read -r reply
fi
exit "$result"
