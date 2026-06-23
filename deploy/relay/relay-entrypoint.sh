#!/bin/sh
set -eu

DATA_DIR="${FERN_DATA_DIR:-/data}"
KEY_FILE="$DATA_DIR/relay.key"
DB_FILE="$DATA_DIR/relay.db"

mkdir -p "$DATA_DIR"

if [ ! -f "$KEY_FILE" ]; then
    echo "[entrypoint] Generating new relay keypair at $KEY_FILE"
    PUBKEY=$(python3 -c "
from fern.crypto.keys import Keypair
k = Keypair.generate()
with open('$KEY_FILE', 'w') as f:
    f.write(k.privkey_hex)
print(k.pubkey_hex)
")
    chmod 600 "$KEY_FILE"
    echo "[entrypoint] ============================================"
    echo "[entrypoint] New relay pubkey: $PUBKEY"
    echo "[entrypoint] BACK UP $KEY_FILE."
    echo "[entrypoint] Losing this file means a new pubkey on restart,"
    echo "[entrypoint] which invalidates client trust pins and event_receipts."
    echo "[entrypoint] ============================================"
else
    echo "[entrypoint] Loading existing key from $KEY_FILE"
fi

exec "$@"
