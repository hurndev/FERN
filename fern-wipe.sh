#!/usr/bin/env bash
# Wipe FERN local storage.
#
# Usage:
#   ./fern-wipe.sh            # wipe CLI only (~/.fern or $FERN_HOME)
#   ./fern-wipe.sh relay      # wipe relay db only (relay.db or $RELAY_DB)
#   ./fern-wipe.sh all        # wipe both
#
# Relay db path defaults to ./relay.db; override with $RELAY_DB or pass as 2nd arg:
#   ./fern-wipe.sh relay /path/to/custom.db

set -euo pipefail

CLI_DIR="${FERN_HOME:-$HOME/.fern}"
RELAY_DB="${2:-${RELAY_DB:-relay.db}}"

wipe_cli() {
    if [ -d "$CLI_DIR" ]; then
        rm -rf "$CLI_DIR"
        echo "wiped CLI: $CLI_DIR"
    else
        echo "CLI dir not present: $CLI_DIR"
    fi
}

wipe_relay() {
    if [ -f "$RELAY_DB" ]; then
        rm -f "$RELAY_DB" "$RELAY_DB-wal" "$RELAY_DB-shm"
        echo "wiped relay db: $RELAY_DB"
    else
        echo "relay db not present: $RELAY_DB"
    fi
}

case "${1:-cli}" in
    cli)   wipe_cli ;;
    relay) wipe_relay ;;
    all)   wipe_cli; wipe_relay ;;
    *) echo "usage: $0 [cli|relay|all] [relay_db_path]"; exit 1 ;;
esac
