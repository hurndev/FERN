#!/usr/bin/env bash
# Wipe FERN local storage.
#
# Usage:
#   ./fern-wipe.sh            # wipe CLI only (~/.fern or $FERN_HOME)
#   ./fern-wipe.sh validator  # wipe validator db only (validator.db or $VALIDATOR_DB)
#   ./fern-wipe.sh all        # wipe both
#
# Validator db path defaults to ./validator.db; override with $VALIDATOR_DB or pass as 2nd arg:
#   ./fern-wipe.sh validator /path/to/custom.db

set -euo pipefail

CLI_DIR="${FERN_HOME:-$HOME/.fern}"
VALIDATOR_DB="${2:-${VALIDATOR_DB:-validator.db}}"

wipe_cli() {
    if [ -d "$CLI_DIR" ]; then
        rm -rf "$CLI_DIR"
        echo "wiped CLI: $CLI_DIR"
    else
        echo "CLI dir not present: $CLI_DIR"
    fi
}

wipe_validator() {
    if [ -f "$VALIDATOR_DB" ]; then
        rm -f "$VALIDATOR_DB" "$VALIDATOR_DB-wal" "$VALIDATOR_DB-shm"
        echo "wiped validator db: $VALIDATOR_DB"
    else
        echo "validator db not present: $VALIDATOR_DB"
    fi
}

case "${1:-cli}" in
    cli)   wipe_cli ;;
    validator) wipe_validator ;;
    all)       wipe_cli; wipe_validator ;;
    *) echo "usage: $0 [cli|validator|all] [validator_db_path]"; exit 1 ;;
esac
