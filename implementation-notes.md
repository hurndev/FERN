# FERN — Implementation Notes

This document captures context for working with the FERN codebase. Read it before writing code. For protocol details, see `spec.md`. For design rationale, see `architecture.md`. For module structure, see `python-architecture.md`.

---

## 1. Getting Started

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest            # 57 tests, ~0.1s
ruff check .      # lint
mypy              # type check (strict mode)
```

The library is in `src/fern/`. The CLI is in `cli/`. Both are installed as an editable package. Two console scripts: `fern` (CLI) and `fern-relay` (standalone relay). A web SPA client (Bracken) lives in `bracken/`.

---

## 2. Thread Model

**There is only one event-loop thread.** All async code runs on the same asyncio event loop. Sync pure functions are called directly from async code. `SqliteStore` exposes async methods for API consistency, but SQLite operations are executed synchronously under a store-local lock with short-lived connections. Do not add threads without a strong reason.

The `WebSocketRelayClient` uses a single-reader model: a `_listen_loop` task reads all Websocket messages. Push messages go to callbacks via `asyncio.ensure_future`. Response messages go to an `asyncio.Queue` where request-response methods block until the matching response arrives.

---

## 3. Critical Gotchas

### 3.1 Canonical serialization is load-bearing

`canonical_serialization(event: Event) -> bytes` is the root of all ID and signature computation. If it's wrong, nothing else works. Key rules:
- Output is a JSON array `[type, group, author, parents, content, ts, tags]` — NOT an object
- `parents` MUST be sorted lexicographically before serialization
- `content` dict keys MUST be sorted recursively (see `sort_keys_recursive()`)
- Arrays inside `content` are NOT sorted (order may be semantic)
- Use `json.dumps(array, separators=(",", ":"), ensure_ascii=False)`
- Do NOT use `json.dumps(..., sort_keys=True)` on the outer array — it's an array with fixed field order, not an object
- Tags MUST be sorted (lexicographic on first element, then second, etc.)

### 3.2 Lowercase hex everywhere

Public keys, event IDs, signatures, hashes are ALL lowercase hex. `hashlib.sha256().hexdigest()` returns lowercase by default. `bytes.hex()` returns lowercase. But `cryptography`'s Ed25519 may output different casing — always apply `.lower()` if converting from a library that doesn't guarantee lowercase.

### 3.3 Ed25519 with `cryptography`

```python
# Generate
priv = Ed25519PrivateKey.generate()
priv_bytes = priv.private_bytes(Raw, Raw, NoEncryption())  # 32 bytes

# Convert to public
pub = priv.public_key()
pub_bytes = pub.public_bytes(Raw, Raw)  # 32 bytes

# Sign — signs message_bytes directly, returns 64 bytes
sig = priv.sign(message_bytes)

# Verify — raises InvalidSignature on failure, does NOT return bool
pub.verify(sig, message_bytes)
```

Always use `Encoding.Raw` + `PrivateFormat.Raw` / `PublicFormat.Raw`. Never use PEM or DER for protocol purposes.

### 3.4 Frozen dataclass defaults

`event.py` uses `field(default_factory=dict)` for `content` and `()` for `parents`. If you add new fields with mutable defaults, always use `field(default_factory=...)`.

### 3.5 Attestation `set_hash` empty-set case

Per `spec.md` § 9.2.3: `set_hash` of an empty set is `sha256_hex(b"")` = `"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"`. This is computed correctly by `compute_set_hash()` — it returns `sha256_hex(b"")` when the input is empty.

### 3.6 Attestation `prev` chain

`prev` is the SHA-256 hash of the previous attestation's canonical serialization. It is NOT the previous attestation's `sig` or any `id` field (attestations don't have an `id`). The `hash_attestation()` function computes it. The `build_attestation(prev=previous_att)` automatically sets this.

### 3.7 Conflict resolution order

State events apply in `(ts, id)` ascending order. `derive_group_state()` sorts by `(e.ts, e.id)`. Do NOT apply events in network arrival order.

### 3.8 Ban expiry uses event-time, not wall-clock

`is_banned_at(pubkey, ts)` checks `entry.until > ts`. If the ban expired at `until=1000` and a `join` event has `ts=1000`, the ban is expired. Always use event timestamps, not `time.time()`.

### 3.9 Disconnected DAG events are storage-only

An event is usable history only if it is connected to the group's `genesis`: genesis is connected, and a non-genesis event is connected only when every parent is already connected. Events with missing parents must be stored for gap healing, but must not be applied to group state, rendered as ordinary chat messages, or used as parents for future events.

This matters for attack resistance. If a client allowed an arbitrary disconnected event to become a head, anyone could publish an event with a fake missing parent and poison all future parent selection. Failed local sends have the same practical risk: keep them retryable, but exclude them from head selection until delivery succeeds.

### 3.10 Monitor pass: two-stage design

The completeness layer has two stages:
1. **Pure `monitor_pass()`** — compares attestation `set_hash` to local known set. Returns `MonitorResult` with `in_sync` flag and `candidates_to_check` tuple.
2. **Async `run_monitor_pass()`** — for each candidate, queries the relay via `get()` to check if it's truly missing. Builds fraud proofs for events with receipts. Writes faults to trust ledger.

The pure function cannot determine which specific events are missing without network I/O — that's the async layer's job.

### 3.11 Receipts are author-local

Receipts are stored on the author's device via `ReceiptStore`. They are NOT events in the DAG. They are shared ONLY when building a fraud proof. Do not add an "auto-gossip receipts" mechanism — the protocol intentionally avoids this complexity.

### 3.12 Fraud proofs are not DAG events

Fraud proofs are standalone objects stored in a side table (`fraud_proofs`) on relays, queryable via `submit_fraud_proof` / `query_fraud_proofs`. They do not affect group state or the DAG.

### 3.13 Relay `subscribe` returns an attestation

Per spec § 10.4.1, the relay responds to `subscribe` with the latest attestation for that group. `WebSocketRelayClient.subscribe()` sends the subscribe message but doesn't wait for a response (the response comes as a push via the listen loop). The `RelayServer._handle_subscribe()` builds and returns the attestation, and adds the client to the subscriber set.

### 3.14 Relay auto-hosts on valid genesis

The `RelayServer._handle_publish()` checks if an event is a `genesis` for an unknown group and auto-hosts it. This avoids the chicken-and-egg problem where a founder couldn't bootstrap a new group.

### 3.15 SqliteStore is synchronous behind an async API

`SqliteStore` methods are async to match the `EventStore` / `ReceiptStore` protocols, but the SQLite calls themselves run synchronously under a store-local lock. Each operation opens a short-lived SQLite connection inside that lock. Do not share a single SQLite connection across `asyncio.to_thread()` worker threads: Python's sqlite connection/thread behavior is easy to break under concurrent relay backfill.

### 3.16 WebSocket connection scheme

The `WebSocketRelayClient.connect()` method auto-prepends `wss://` to URLs that don't start with `ws://` or `wss://`. Use `ws://localhost:PORT` for local testing (no TLS). Use `wss://` for production.

### 3.17 `_awaiting_response` flag in WebSocketRelayClient

The single-reader model routes push messages (`event`, `attestation`) to callbacks and response messages to the queue. But `sync()` and `get()` responses are ALSO `event`-type messages. The `_awaiting_response` flag tells the listen loop to route ALL messages to the queue when a request-response method is active, preventing sync/get/attestation responses from being swallowed by push callbacks. Set it `True` before sending the request, `False` in the `finally` block.

Bracken's browser relay client has a related trap: pending request resolvers must be keyed by response type. A `publish` request expects a `receipt`; if an unrelated pushed message or error consumes the resolver first, the UI can show "sending" then "failed" even though the relay accepted the event.

### 3.18 Relay `_hosted_groups` reconstruction

`_hosted_groups` is an in-memory set that starts empty. On startup, the relay calls `await self._store.get_hosted_groups()` (added to both `EventStore` Protocol and all implementations) to populate it from the database. Without this, a relay restart causes all publishes to fail with "group not hosted" even though the events exist in the DB.

### 3.19 `FERN_HOME` env var

`cli/config.py` uses `FERN_HOME` env var to override the default `~/.fern` directory. All CLI data (config + per-group SQLite caches) lives under that path. When unset, falls back to `~/.fern`. This allows running multiple isolated CLI instances on the same machine:

### 3.20 Backfill vs Publish

`backfill` stores an event without broadcasting. `publish` stores and broadcasts. Use `backfill` when healing or seeding a relay with historical events. Use `publish` for newly-created local events. The relay deduplicates both: if it already has the event, it returns a receipt without re-verifying, re-storing, or broadcasting.

### 3.21 Sync lock is advisory and lease-based

The sync lock prevents thundering herd during backfill. It is per-group, lease-based (30s TTL, lazy expiry), and advisory. Clients that do not support it can still backfill; relay-side dedup makes this safe but less efficient.

CLI commands use opportunistic lock behavior: if another client holds the lock, they skip that relay and exit promptly. Bracken uses event-driven retry gates: when denied, it records `nextRetryAt` and retries on a later attestation/reconnect/manual sync trigger after the lease window.

---

## 4. CLI Patterns

### 4.1 Each command is one-shot

Every CLI command follows the pattern:
```python
def command(...):
    asyncio.run(_command(...))

async def _command(...):
    config = load_config()
    group_pubkey, group_info = resolve_group(group_id, config)
    transports = await connect_transports(relay_urls)
    # ... do work ...
    for t in transports:
        await t.close()
```

No persistent connections between commands. No daemon. `watch` is the only long-running command (runs an event loop with callbacks until Ctrl+C).

### 4.2 Shared transport helper

`connect_transports(urls)` in `cli/config.py` is the single point for connecting to relays. It:
1. Creates `WebSocketRelayClient` for each URL
2. Calls `connect()`
3. Returns only successfully connected transports (silently skips failures)

Metadata fetching is done separately by callers that need it. All CLI commands use this helper. If you add a new command that needs relays, use it.

### 4.3 Group identification

Groups are numbered 1, 2, 3... in join order. `resolve_group(group_id, config)` handles:
- Numeric strings: indexes into `config["group_order"]`
- 64-char lowercase hex: direct pubkey lookup
- Fallback: checks `config["groups"]` keys

### 4.4 Per-group SQLite cache

Each group gets `~/.fern/cache/<pubkey>.sqlite`. Commands that need the latest state (`read`, `group info`, `verify`) sync from relays into this cache before operating. The cache persists across CLI invocations.

### 4.5 Console scripts

Two entry points:
- `fern` → `cli.main:main` — main CLI
- `fern-relay` → `cli.relay_main:main` — starts a WebSocket relay server directly

Relay start commands print the local WebSocket address, mapping wildcard binds such as `0.0.0.0` or `::` to a usable `ws://localhost:PORT` display URL for local testing.

---

## 5. Bracken Patterns

Bracken is the browser SPA in `bracken/`. It stores identity, events, receipts, relay pins, trust ledger, and metadata in IndexedDB.

Current UI and protocol behavior:
- Usernames/nicknames are not unique. `chat.nickname_set` is per-author display metadata; two users can display the same name.
- The current user is marked with `(You)` in message headers and the member list.
- Admin styling is color-only in lists/messages; textual `(Admin)` is reserved for full user profiles.
- Settings call the export secret a "private key", not a "seed". Import belongs in setup only.
- Logging out wipes Bracken identity and local IndexedDB cache, then returns to setup.
- The private key reveal control is an icon-only eye button.
- Group info exposes the full group pubkey, invite link, description, and canonical relays with compact copy controls.
- If there are no connected relays, sends remain local retryable deliveries. Failed local sends are shown as failed with a retry action and are excluded from future parent selection.
- The header relay badge shows the canonical relay count: red for one, orange for two, green for three or more.
- Admin-only slash commands include `/kick`, `/ban`, `/unban`, `/invite`, `/promote`, `/demote`, `/relay-add`, `/relay-remove`, `/name`, `/description`, `/channel-create`, and `/channel-delete`. `/relay-add` and `/relay-remove` accept multiple URLs separated by spaces or commas.
- Bracken treats chat channel IDs as stable. The genesis channel ID is `"general"`; new channels use the `chat.channel_create` event ID. Channel names are mutable display metadata, not identifiers.

---

## 6. Testing Patterns

### 6.1 Pure unit tests (the bulk)

Most tests are pure and need no async:
```python
def test_canonical_serialization_is_deterministic():
    e1 = Event(...)
    assert compute_id(e1) == compute_id(e1)
```

### 6.2 Integration tests with FakeRelay

For tests needing relay interaction without a network:
```python
network = FakeRelayNetwork()
relay_a, relay_b, relay_c = network.spawn(count=3)
receipt = await relay_a.publish(event)
attestation = await relay_a.request_attestation(group)
```

`FakeRelay` implements the same `RelayTransport` Protocol as `WebSocketRelayClient`, so the same assertions work.

### 6.3 Integration tests with real localhost relay

Start `fern-relay` on a port, then use `WebSocketRelayClient` to connect. Use `ws://` for local testing.

### 6.4 Deterministic test keypairs

`conftest.py` provides fixed-seed keypairs. This ensures tests are reproducible across runs — no random key generation in tests.

### 6.5 Test commands

```bash
pytest -v                                     # verbose
pytest -x                                     # stop at first failure
pytest -k "test_ban"                          # filter by name
pytest --cov=fern --cov-report=term-missing   # coverage
```

---

## 7. Code Style

### 7.1 Linting

`ruff` enforces:
- Line length: 100
- Target: Python 3.11+
- No commented-out code
- No bare `except:`

Run `ruff check .` before committing. Auto-fix with `ruff check --fix .`.

### 7.2 Type Checking

`mypy --strict`. All source files pass. When adding new code, ensure type annotations are complete. Use `Protocol` for interfaces. Prefer `Mapping`/`Sequence` over `dict`/`list` for read-only parameters.

### 7.3 Imports

Standard order (enforced by ruff): stdlib → third-party → local. Use absolute imports (`from fern.crypto.keys import Keypair`). Use `from __future__ import annotations` at the top of each module.

### 7.4 Docstrings

Pure functions get a one-line docstring. Async functions get a docstring explaining side effects. Don't write "Verify an event" — say what the function actually validates and what errors it raises.

### 7.5 No comments explaining what code does

Use docstrings and clear naming. Comments should explain WHY (when non-obvious).

---

## 8. What's Explicitly Out of Scope

- Threshold founder signing (founder single-key)
- Merkle exclusion proofs (simple sorted-set-hash attestations)
- Snapshots (verify from genesis)
- Cross-client reputation propagation (local trust ledgers)
- Relay-side authorization enforcement (all moderation is client-side)
- BIP-39 mnemonic backups (plain hex private key storage)
- E2E encryption (public chats only)
- Protocol versioning field
- BIP-39 mnemonic backups (plain hex private key storage)

Note: `bracken/` is a separate TypeScript SPA (Vite + React) implementing the same FERN protocol as a web client. It uses tweetnacl-js for Ed25519 and idb for IndexedDB persistence. It is not part of the Python package.

---

## 9. Quick Reference

### 9.1 Key constants

- Max event size: 32 KiB (enforced by relay, not client)
- Pubkey: 32 bytes → 64-char lowercase hex
- Signature: 64 bytes → 128-char lowercase hex
- Hash output: 32 bytes → 64-char lowercase hex
- Empty-set `set_hash`: `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
- Suggested relay GC threshold: N=1000
- Suggested attestation interval: 5 seconds or 100 events
- Sync lock TTL: 30 seconds
- Sync lock renewal interval: 60% of TTL
- Backfill batch size: 10 concurrent events
- Default K (relays per publish): 3 (all canonical relays)
- Default K_min (receipts for safe ack): 2
- `FERN_HOME` env var: overrides `~/.fern` for CLI data storage
- Relay log formatter: coloured output by level (INFO=green, WARN=yellow, ERROR=red), `--no-color` to disable
- Relay metadata endpoint: HTTP GET on same host/port (wss→https scheme swap), returns JSON with CORS headers
- `fern-relay --key-file PATH`: load the relay's 64-char hex private key from a file instead of generating one. The default behaviour (no flag) mints a fresh keypair every start, which breaks client trust pins and invalidates outstanding receipts — only acceptable for ephemeral/dev use. For a long-lived relay, pass `--key-file` and persist the keyfile outside the container. The `deploy/relay/relay-entrypoint.sh` wrapper handles first-run generation automatically.

### 9.2 Event type names

Protocol types (no dot): `genesis`, `join`, `leave`, `invite`, `kick`, `ban`, `unban`, `admin_add`, `admin_remove`, `relay_update`, `metadata_update`

`chat` namespace (official app): `chat.message`, `chat.reaction`, `chat.nickname_set`, `chat.channel_create`, `chat.channel_update`, `chat.channel_delete`, `chat.settings_update`

Future namespaces: `<appname>.<type>` (e.g., `poll.vote`, `schedule.event`)

### 9.3 Canonical serialization order

- Event: `[type, group, author, sorted(parents), sorted_content_recursively, ts, sorted(tags)]`
- Receipt: `[event_id, group, relay, ts]`
- Attestation: `[group, relay, set_hash, sorted(tips), count, prev_or_null, ts]`
- Fraud proof: `[type, group, relay, event_id, event_array, receipt_array, evidence]`

### 9.4 WebSocket actions

| Action | Purpose |
|---|---|
| `subscribe` | Start receiving events + attestations for a group |
| `publish` | Submit an event; relay validates and returns receipt |
| `get` | Request a specific event by ID |
| `sync` | Bulk-fetch events since a timestamp |
| `sync_ids` | Bulk-fetch event IDs only (no event bodies) |
| `sync_lock` | Acquire/renew per-group backfill coordination lock |
| `sync_unlock` | Release sync lock |
| `backfill` | Store an event without broadcasting to subscribers |
| `attestation` | Request relay's latest attestation |
| `submit_fraud_proof` | Submit a fraud proof for storage and gossip |
| `query_fraud_proofs` | Query stored fraud proofs |
| `unsubscribe` | Stop receiving events for a group |

### 9.5 CLI commands quick reference

```bash
# Identity
fern init
fern whoami

# Groups
fern group create --name "Chat" --relay ws://localhost:8765
fern group join fern:<pubkey>@<relays>
fern group list
fern group info 1
fern group members 1
fern group leave 1
fern group nickname 1 "Alice"

# Moderation (admin-only)
fern group kick 1 <pubkey>
fern group ban 1 <pubkey> [--until <ts>] [--reason <text>]
fern group unban 1 <pubkey>
fern group invite 1 <pubkey>
fern group admin-add 1 <pubkey>
fern group admin-remove 1 <pubkey>
fern group relay-update 1 ws://new-relay:9000

# Messaging
fern post 1 "hello"
fern post --channel general 1 "hello"
fern read 1 [--show-rejected]
fern watch 1 [--show-rejected]

# Relay
fern relay start --port 8765 --store relay.db
fern relay info ws://localhost:8765

# DAG viewer
fern dag --db relay.db

# Standalone relay
fern-relay --port 8765 --store relay.db
```
