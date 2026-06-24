#!/bin/sh
set -eu

DATA_DIR="${FERN_DATA_DIR:-/data}"
CONFIG_FILE="$DATA_DIR/config.json"
KEY_FILE="$DATA_DIR/relay.key"

mkdir -p "$DATA_DIR"

if [ ! -f "$CONFIG_FILE" ]; then
    echo "[entrypoint] No config found. Running 'fern-relay init' to generate keypair and default config."
    fern-relay init --config "$CONFIG_FILE" --store "$DATA_DIR/relay.db"
    chmod 600 "$KEY_FILE" 2>/dev/null || true
    PUBKEY=$(python3 -c "
from fern.relay.config import load_config, load_keypair
c = load_config()
print(load_keypair(c).pubkey_hex)
" 2>/dev/null || echo "(unknown)")
    echo "[entrypoint] ============================================"
    echo "[entrypoint] New relay pubkey: $PUBKEY"
    echo "[entrypoint] BACK UP $KEY_FILE."
    echo "[entrypoint] Losing this file means a new pubkey on restart,"
    echo "[entrypoint] which invalidates client trust pins and event_receipts."
    echo "[entrypoint] ============================================"
else
    echo "[entrypoint] Loading config from $CONFIG_FILE"
fi

exec "$@" --config "$CONFIG_FILE"