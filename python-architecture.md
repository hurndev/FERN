# FERN Python Implementation — Architecture

This document describes the architecture of `fern`, the Python reference implementation of the FERN protocol. The library is modular, testable, and reusable across multiple frontends: a CLI tool ships alongside it; a web app or desktop app can use the same `GroupSession` API.

See `spec.md` for the wire-level protocol details and `architecture.md` for the high-level protocol design.

---

## 1. Design Principles

### 1.1 Pure / Impure Boundary

- **Pure**: crypto, hashing, canonical serialization, signature verification, event construction, DAG operations, state machine fold, attestation/receipt/fraud-proof building and verification. All synchronous. Trivially testable (input → expected output).
- **Impure-async**: WebSocket relay client/server, SQLite storage (wrapped in `asyncio.to_thread`), client orchestration (`GroupSession`, publishing, subscribing, monitor runner).
- **Impure-sync**: the `sqlite3` module itself — wrapped via `asyncio.to_thread` in `SqliteStore`.

~80% of the code is pure. Tests target pure functions directly; only integration tests need I/O.

### 1.2 No Global State

All dependencies are passed explicitly. No module-level singletons. Multiple `GroupSession` instances can run in one process (useful for tests and multi-group clients).

### 1.3 Interfaces at the Boundaries

Where pure logic needs I/O, it uses a `Protocol` (PEP 544). Tests substitute in-memory fakes that implement the same Protocol.

- `EventStore` — event storage (MemoryStore for tests, SqliteStore for production)
- `ReceiptStore` — receipt storage (built into MemoryStore and SqliteStore)
- `RelayTransport` — relay client (WebSocketRelayClient for real connections, FakeRelay for tests)

### 1.4 Immutability by Default

`Event`, `Receipt`, `Attestation`, `GroupState`, `BanEntry`, `FraudProof` are `@dataclass(frozen=True)`. Mutating operations return copies. (Exception: `Keypair` is a plain class wrapping `cryptography`'s internal state.)

### 1.5 Async at the Edge, Sync in the Core

The state machine, crypto, serialization, and completeness pure logic are sync. Only the I/O layers are async. The CLI wraps everything in `asyncio.run()`.

### 1.6 Layered Dependencies

```
CLI / apps
    ↓
client.session (orchestration) · relay server
    ↓
transport (websocket / fake) · storage (sqlite / memory) · completeness.backfill
    ↓
completeness (receipts, attestations, fraud_proofs, monitor, trust_ledger) · state.machine · dag
    ↓
events · identity · chat
    ↓
crypto
```

---

## 2. Package Structure

### 2.1 Library (`src/fern/`)

```
src/fern/
├── __init__.py
├── errors.py                    # Exception hierarchy
├── crypto/
│   ├── keys.py                  # Keypair class (Ed25519 generate, sign, verify)
│   ├── hashes.py                # sha256_hex(data: bytes) → str
│   └── encoding.py              # is_valid_pubkey_hex, is_valid_sig_hex, etc.
├── events/
│   ├── event.py                 # Event dataclass (frozen)
│   ├── serialization.py         # canonical_serialization(), compute_id(), sign_event()
│   ├── validation.py            # verify_event(), is_well_formed()
│   ├── build.py                 # build_event() helper
│   └── types.py                 # ProtocolTypes, ChatTypes constants
├── identity/
│   ├── user.py                  # UserIdentity dataclass (wraps Keypair)
│   ├── group.py                 # GroupKeypair dataclass
│   └── relay.py                 # RelayIdentity dataclass
├── dag/
│   ├── heads.py                 # compute_heads(), parent_to_children()
│   ├── gaps.py                  # find_missing_parents()
│   └── cycle_check.py           # has_cycle()
├── state/
│   ├── types.py                 # GroupState, BanEntry dataclasses
│   ├── authorization.py         # is_authorised()
│   └── machine.py               # derive_group_state(), apply_event()
├── completeness/
│   ├── receipts.py              # Receipt dataclass, build_receipt(), verify_receipt()
│   ├── attestations.py          # Attestation dataclass, build/verify, compute_set_hash()
│   ├── fraud_proofs.py          # FraudProof dataclass, build/verify, compute_fraud_proof_id()
│   ├── monitor.py               # monitor_pass() pure logic, MonitorResult
│   ├── trust_ledger.py          # TrustLedger, RelayTrustEntry, Fault
│   └── backfill.py              # backfill_missing() async helper
├── storage/
│   ├── interfaces.py            # EventStore Protocol, ReceiptStore Protocol
│   ├── memory.py                # MemoryStore (in-memory, used in tests)
│   └── sqlite_store.py          # SqliteStore (disk-backed, uses asyncio.to_thread)
├── transport/
│   ├── interfaces.py            # RelayTransport Protocol, RelayMetadata dataclass
│   ├── websocket_client.py      # WebSocketRelayClient (single-reader model, response queue)
│   ├── websocket_server.py      # RelayServer (subscribe, sync, publish, attestations, fraud proofs)
│   ├── fake.py                  # FakeRelay (in-process relay for tests)
│   ├── wire.py                  # Message dataclasses (not currently used by client/server)
│   └── metadata.py              # fetch_relay_metadata() async helper
├── client/
│   ├── session.py               # GroupSession — per-group client orchestration
│   ├── bootstrap.py             # fetch_genesis(), initial_sync()
│   ├── publisher.py             # publish_event() — parallel publish + receipt collection
│   ├── subscriber.py            # subscribe_to_relays(), unsubscribe_from_relays()
│   └── monitor_runner.py        # run_monitor_pass() — async investigation + trust ledger update
├── relay/
│   ├── store.py                 # RelayStore wrapper
│   ├── gc.py                    # garbage_collect() — tip cleanup with unreferenced-for-N check
│   ├── attestation_loop.py      # Periodic attestation issuance with prev chain tracking
│   └── metadata_handler.py      # build_metadata() helper
├── chat/
│   ├── messages.py              # build_chat_message(), is_chat_message()
│   ├── reactions.py             # build_reaction()
│   └── nicknames.py             # build_nickname_set()
└── apps/
    └── __init__.py              # Reserved for future app namespaces
```

### 2.2 CLI (`cli/`)

```
cli/
├── __init__.py
├── main.py                      # Entry point (fern console_script)
├── relay_main.py                # fern-relay console_script (with coloured logging)
├── config.py                    # Config loading, group resolution, transport helper
│                                #   FERN_HOME env var overrides ~/.fern default
├── output.py                    # print_success(), print_error()
├── dag_viewer.py                # Zero-dependency DAG web viewer (stdlib http.server + SSE)
├── fern-wipe.sh                 # Convenience script to wipe CLI/relay storage
└── commands/
    ├── init.py                  # fern init — generate identity
    ├── whoami.py                # fern whoami — show pubkey
    ├── group.py                 # fern group create|join|list|info|members|leave
    │                            #   kick|ban|unban|invite|mod-add|mod-remove|relay-update|nickname
    ├── post.py                  # fern post <group> <text> (syncs state, checks auth before publishing)
    ├── read.py                  # fern read <group> (shows mod actions inline, nicknames, auth filtering)
    ├── watch.py                 # fern watch <group> (shows mod actions, nicknames, auth filtering)
    ├── verify.py                # fern verify <group>
    ├── relay.py                 # fern relay start|info
    └── dag.py                   # fern dag --db <path> — launch the DAG viewer for any SQLite store
```

### 2.3 Tests (`tests/`)

```
tests/
├── conftest.py                  # Shared fixtures: keypairs, sample_genesis, memory_store
├── unit/
│   ├── crypto/test_crypto.py
│   ├── events/test_events.py
│   ├── events/test_serialization_property.py
│   ├── dag/test_dag.py
│   ├── state/test_state.py
│   ├── completeness/test_completeness.py
│   └── chat/test_chat.py
└── integration/
    ├── test_fake_relay.py
    ├── test_event_roundtrip.py
    └── test_censorship_detection.py
```

---

## 3. Layer Details

### 3.1 `fern.crypto` (Pure)

```python
class Keypair:
    def __init__(self, privkey_bytes: bytes) -> None: ...
    @classmethod def generate(cls) -> Keypair: ...
    @classmethod def from_privkey(cls, privkey: bytes) -> Keypair: ...
    @property def pubkey_hex(self) -> str: ...
    @property def privkey_hex(self) -> str: ...
    def sign(self, message: bytes) -> bytes: ...
    def sign_detached(self, message: bytes) -> str: ...         # hex-encoded signature
    @staticmethod def verify_static(pubkey_bytes, message, sig) -> bool: ...

def sha256_hex(data: bytes) -> str: ...                          # lowercase hex output
def is_valid_pubkey_hex(s: str) -> bool: ...                     # 64-char lowercase hex
def is_valid_event_id_hex(s: str) -> bool: ...                   # 64-char lowercase hex
def is_valid_sig_hex(s: str) -> bool: ...                        # 128-char lowercase hex
```

### 3.2 `fern.events` (Pure)

```python
@dataclass(frozen=True)
class Event:
    type: str
    group: str
    author: str
    parents: tuple[str, ...] = ()
    content: dict = field(default_factory=dict)
    ts: int = 0
    tags: tuple[tuple[str, ...], ...] = ()
    id: str | None = None
    sig: str | None = None

    @property def is_genesis(self) -> bool: ...

def canonical_serialization(event: Event) -> bytes: ...    # [type, group, author, parents, content, ts, tags]
def compute_id(event: Event) -> str: ...
def sign_event(event: Event, keypair, *, is_genesis=False) -> Event: ...

def verify_event(event: Event) -> None:     # raises VerificationError subclasses
def is_well_formed(event: Event) -> bool: ...

def build_event(*, type, group, author_keypair, parents, content, ts, tags,
                group_keypair=None) -> Event: ...
```

Key canonical serialization rules:
- Array is `[type, group, author, sorted(parents), sorted_content, ts, sorted(tags)]`
- `parents` sorted lexicographically
- `content`: dict keys sorted recursively (`sort_keys_recursive`)
- Arrays inside `content` are NOT sorted (order is semantic)
- No whitespace: `json.dumps(array, separators=(",", ":"), ensure_ascii=False)`
- `id = sha256_hex(canon_bytes)`, `sig = ed25519_sign(privkey, canon_bytes)`

### 3.3 `fern.state` (Pure)

```python
@dataclass(frozen=True)
class GroupState:
    members: frozenset[str]
    joined: frozenset[str]
    banned: Mapping[str, BanEntry]
    mods: frozenset[str]
    relays: tuple[str, ...]
    metadata: Mapping[str, str]
    public: bool

    def is_banned_at(self, pubkey: str, ts: int) -> bool: ...
    def can_post(self, pubkey: str, ts: int) -> bool: ...
    def can_moderate(self, pubkey: str) -> bool: ...

def derive_group_state(events: Iterable[Event]) -> tuple[GroupState, list[Event]]: ...
def apply_event(state: GroupState, event: Event) -> GroupState: ...
def is_authorised(state: GroupState, event: Event) -> bool: ...
```

State is folded in `(ts, id)` canonical linearisation order over the genesis-connected event set only. Events with missing parents are retained in storage for gap healing, but they must not be applied to state until their complete parent chain connects to genesis. Conflict resolution: events at same `ts` are ordered by ascending `id`. Last-writer-wins per field.

Ban semantics: a ban persists until `unban` or `until` expiry. A banned user cannot `join`. A `kick` does not ban — user can re-join.

### 3.4 `fern.dag` (Pure)

```python
def compute_connected_event_ids(events: Iterable[Event]) -> frozenset[str]: ...
def compute_connected_heads(
    events: Iterable[Event], *, excluded_ids: Iterable[str] = ()
) -> frozenset[str]: ...
def find_missing_parents(events: Iterable[Event]) -> frozenset[str]: ...
def has_cycle(events: Iterable[Event]) -> bool: ...
```

Connectedness is a recursive genesis gate: `genesis` is connected, and a non-genesis event is connected only when every parent is already connected. `compute_connected_heads()` returns heads from that connected subset and excludes local-only or failed publish attempts. This prevents a disconnected event from becoming the parent of future events.

### 3.5 `fern.completeness` (Pure logic + async orchestration)

```python
# Receipts (pure)
@dataclass(frozen=True)
class Receipt:
    event_id: str; group: str; relay: str; ts: int; sig: str
def build_receipt(*, event, relay_keypair, ts) -> Receipt: ...
def verify_receipt(receipt: Receipt) -> bool: ...

# Attestations (pure)
@dataclass(frozen=True)
class Attestation:
    group: str; relay: str; set_hash: str; tips: tuple[str, ...]
    count: int; prev: str | None; ts: int; sig: str
def build_attestation(*, group, relay_keypair, known_set, tips, count, prev, ts) -> Attestation: ...
def verify_attestation(attestation: Attestation, prev: Attestation | None = None) -> bool: ...
def compute_set_hash(event_ids: Iterable[str]) -> str: ...
def hash_attestation(attestation: Attestation) -> str: ...

# Fraud proofs (pure)
@dataclass(frozen=True)
class FraudProof:
    type: str; group: str; relay: str; event_id: str
    event: Event | None; receipt: Receipt | None; evidence: str
def build_fraud_proof(*, relay, event, receipt, evidence) -> FraudProof: ...
def verify_fraud_proof(proof: FraudProof) -> bool: ...
def compute_fraud_proof_id(proof: FraudProof) -> str: ...

# Monitor (pure + async)
@dataclass(frozen=True)
class MonitorResult:
    in_sync: bool
    faults: tuple[Fault, ...] = ()
    divergent_relays: tuple[str, ...] = ()
    candidates_to_check: tuple[str, ...] = ()

def monitor_pass(*, local_known_set, local_receipts_for_relay, new_attestation,
                 prev_attestation, relay_pubkey, sibling_attestations, now_ts) -> MonitorResult: ...
async def run_monitor_pass(*, relay, attestation, local_known_set, receipts_for_relay,
                           trust_ledger, sibling_attestations) -> MonitorResult: ...

# Trust ledger
@dataclass class TrustLedger: entries: dict[str, RelayTrustEntry]
def add_fault(self, relay_pubkey, fault): ...
```

The monitor works in two stages:
1. **Pure `monitor_pass`**: compares `set_hash` to local known set, checks attestation chain, records sibling divergence. If `in_sync=False`, returns `candidates_to_check`.
2. **Async `run_monitor_pass`**: for each candidate, queries the relay via `get()` to determine which events are truly missing. For events with a receipt → `missing_event_with_receipt` fault (fraud). Without receipt → `missing_event_no_receipt` fault (backfill candidate).

### 3.6 `fern.storage` (I/O with Protocols)

```python
class EventStore(Protocol):
    async def put_event(self, event: Event) -> None: ...
    async def get_event(self, event_id: str) -> Event | None: ...
    async def has_event(self, event_id: str) -> bool: ...
    def iter_all_events(self) -> AsyncIterator[Event]: ...
    def iter_group_events(self, group: str) -> AsyncIterator[Event]: ...
    def iter_since(self, group: str, since_ts: int) -> AsyncIterator[Event]: ...
    async def count_events(self, group: str) -> int: ...
    async def get_tips(self, group: str) -> list[str]: ...
    async def get_known_set(self, group: str) -> frozenset[str]: ...
    async def get_parent_map(self, group: str) -> Mapping[str, frozenset[str]]: ...
    async def get_hosted_groups(self) -> list[str]: ...
    async def delete_event(self, event_id: str) -> None: ...

class ReceiptStore(Protocol):
    async def put_receipt(self, event_id: str, relay_pubkey: str, receipt: Receipt) -> None: ...
    async def get_receipt(self, event_id: str, relay_pubkey: str) -> Receipt | None: ...
    def iter_receipts_for_event(self, event_id: str) -> AsyncIterator[Receipt]: ...
```

Two implementations:
- `MemoryStore` — in-memory dicts. Used in tests and ephemeral CLI invocations.
- `SqliteStore` — SQLite-backed. All sync `sqlite3` calls wrapped in `asyncio.to_thread()`. Schema includes tables for `events`, `parent_refs`, `receipts`, `fraud_proofs`, and `attestations_issued`. Used both as relay event store and per-group client cache at `~/.fern/cache/<pubkey>.sqlite`. `get_hosted_groups()` queries distinct `group_pubkey` values so the relay can reconstruct its hosted groups from disk on startup.

### 3.7 `fern.transport` (I/O with Protocols)

```python
class RelayTransport(Protocol):
    url: str
    relay_pubkey: str
    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def fetch_metadata(self) -> RelayMetadata: ...
    async def subscribe(self, group: str) -> None: ...
    async def publish(self, event: Event) -> Receipt: ...
    async def get(self, event_id: str) -> Event | None: ...
    def sync(self, group: str, since_ts=None) -> AsyncIterator[Event]: ...
    async def request_attestation(self, group: str) -> Attestation: ...
    async def submit_fraud_proof(self, proof: FraudProof) -> str: ...
    def query_fraud_proofs(self, *, relay=None, group=None) -> AsyncIterator[FraudProof]: ...
    def on_event(self, callback) -> None: ...
    def on_attestation(self, callback) -> None: ...
```

Three implementations:
- `WebSocketRelayClient` — real WSS/WS client. Uses a single-reader model: a `_listen_loop` reads all messages, pushes route to callbacks, responses go to an `asyncio.Queue` for request-response correlation. Uses `_awaiting_response` flag so `sync`/`get`/`request_attestation` responses are correctly routed to the queue rather than being swallowed by push callbacks.
- `RelayServer` — real WSS/WS server. Implements subscribe (tracks connections, pushes events/attestations), sync (streams events + `sync_complete`), query_fraud_proofs (streams + `query_complete`). Auto-hosts groups on valid genesis. Serves an HTTP metadata endpoint (with CORS headers) for browser clients. Reconstructs `_hosted_groups` from the database on startup via `get_hosted_groups()`. Uses structured logging with a coloured formatter.
- `FakeRelay` — in-process relay for tests. Implements the same `RelayTransport` Protocol. Tracks `_last_attestations` per group for the prev chain.

### 3.8 `fern.client` (Async Orchestration)

```python
class GroupSession:
    def __init__(self, *, user: UserIdentity, store: EventStore,
                 receipt_store: ReceiptStore, trust_ledger=None): ...
    @property def state(self) -> GroupState | None: ...
    @property def trust_ledger(self) -> TrustLedger: ...

    async def join_group(self, group_pubkey, transports) -> GroupState: ...
    async def publish(self, event: Event) -> tuple[Event, list[Receipt]]: ...
    async def refresh_state(self) -> GroupState | None: ...
    async def get_known_set(self) -> frozenset[str]: ...
    async def close(self) -> None: ...

    def on_event(self, callback) -> None: ...
    def on_attestation(self, callback) -> None: ...
    def on_state_change(self, callback) -> None: ...
```

`GroupSession.join_group()` handles the full bootstrap: connect transports, fetch genesis, initial sync from all relays, derive state, subscribe for live events, register event/attestation handlers. `_handle_event()` updates `_state` when admin events arrive; `_handle_attestation()` runs the monitor pass.

Helper modules:
- `publisher.py` — `publish_event()`: parallel publish to all transports via `asyncio.gather`, collects receipts, stores them if a receipt store is provided.
- `bootstrap.py` — `fetch_genesis()` walks DAG tips back to genesis via `get` requests; falls back to `sync`. `initial_sync()` fetches all events from all transports into a local store.
- `subscriber.py` — `subscribe_to_relays()` calls `transport.subscribe(group)` on each transport.
- `monitor_runner.py` — `run_monitor_pass()`: runs pure `monitor_pass`, then asynchronously investigates candidate events by querying the relay, writes faults to trust ledger.

---

## 4. CLI Architecture

The CLI is a thin layer over `fern` library calls. Each command is a single `asyncio.run(do_command(...))` invocation — connect, act, display, exit. No daemon or persistent connections between commands.

### 4.1 Group Identification

Groups are numbered 1, 2, 3... in join order. `config.json` stores a `group_order` list. The `resolve_group(group_id, config)` function in `cli/config.py` handles numeric IDs, full 64-char hex pubkeys, and direct key lookup.

### 4.2 Config

```json
{
  "user_privkey_hex": "...",
  "group_order": ["<pubkey1>", "<pubkey2>"],
  "groups": {
    "<pubkey1>": {
      "relays": ["ws://relay.example.com"],
      "cache_path": "~/.fern/cache/<pubkey1>.sqlite",
      "joined": true
    }
  }
}
```

`cli/config.py` provides: `load_config()`, `save_config()`, `get_cache_path()`, `resolve_group()`, `parse_group_address()`, `add_group_to_order()`, `connect_transports()` (shared async helper for connecting to relay URLs).

### 4.3 Shared Transport Helper

`connect_transports(urls: list[str]) -> list[WebSocketRelayClient]` is the single point for all relay connections in CLI commands. It:
1. Creates a `WebSocketRelayClient` for each URL
2. Calls `connect()` and `fetch_metadata()`
3. Returns only successfully connected transports (failed ones are silently skipped)

### 4.4 Command Table

| Command | Signature | Implementation |
|---|---|---|
| `fern init` | (no args) | `cli/commands/init.py` — generates `UserIdentity`, saves to config |
| `fern whoami` | (no args) | `cli/commands/whoami.py` — prints pubkey from config |
| `fern group create` | `--name X [--public/--private] [--relay URL]...` | `cli/commands/group.py` — builds genesis, publishes to relays, assigns number. If no `--relay`, prompts interactively from known relays. Caches genesis locally. |
| `fern group join` | `<address>` | `cli/commands/group.py` — parses `fern:<pubkey>@<relays>`, fetches genesis, syncs full history, publishes `join` event |
| `fern group list` | (no args) | `cli/commands/group.py` — prints numbered list from config |
| `fern group info` | `<group>` | `cli/commands/group.py` — syncs from relays, derives state, prints full pubkey and invite link |
| `fern group members` | `<group>` | `cli/commands/group.py` — syncs, prints full pubkeys with nicknames and roles/bans |
| `fern group leave` | `<group>` | `cli/commands/group.py` — publishes `leave` event, updates config |
| `fern group kick` | `<group> <target>` | `cli/commands/group.py` — mod: publishes `kick` event |
| `fern group ban` | `<group> <target> [--until ts] [--reason text]` | `cli/commands/group.py` — mod: publishes `ban` event |
| `fern group unban` | `<group> <target>` | `cli/commands/group.py` — mod: publishes `unban` event |
| `fern group invite` | `<group> <invitee>` | `cli/commands/group.py` — mod: publishes `invite` event |
| `fern group mod-add` | `<group> <target>` | `cli/commands/group.py` — mod: publishes `mod_add` event |
| `fern group mod-remove` | `<group> <target>` | `cli/commands/group.py` — mod: publishes `mod_remove` event |
| `fern group relay-update` | `<group> <url>...` | `cli/commands/group.py` — mod: publishes `relay_update` event |
| `fern group nickname` | `<group> <name>` | `cli/commands/group.py` — publishes `chat.nickname_set` event |
| `fern post` | `[--channel c] [--reply-to id] <group> <text>` | `cli/commands/post.py` — syncs, derives state, checks auth (joined + not banned), publishes |
| `fern read` | `[--channel c] [-n N] [--show-rejected] <group>` | `cli/commands/read.py` — syncs, filters by auth, shows mod actions inline, shows nicknames |
| `fern watch` | `[--channel c] [--show-rejected] <group>` | `cli/commands/watch.py` — subscribes, shows mod actions and nicknames live (Ctrl+C stops) |
| `fern verify` | `<group>` | `cli/commands/verify.py` — requests attestations, runs monitor pass, prints trust ledger |
| `fern relay start` | `--port N --store X [--log-level L] [--no-color]` | `cli/commands/relay.py` — starts a WebSocket relay server with coloured logging |
| `fern relay info` | `<url>` | `cli/commands/relay.py` — fetches relay metadata |
| `fern dag` | `--db <path> [--host H] [--port P]` | `cli/commands/dag.py` — launches the zero-dependency DAG web viewer for any SQLite store |
| `fern-relay` | `--port N --store X [--log-level L] [--no-color]` | `cli/relay_main.py` — standalone relay server with coloured logging |

### 4.5 Per-Group SQLite Cache

Each group gets its own SQLite database at `~/.fern/cache/<group_pubkey>.sqlite`. Commands that need the latest state (`read`, `group info`, `group members`) sync from relays into this cache before operating. `post` reads heads from the cache. `read` reads messages from the cache after sync. The cache is durable across invocations.

### 4.6 Console Scripts

Two entry points in `pyproject.toml`:
- `fern = "cli.main:main"` — the main CLI
- `fern-relay = "cli.relay_main:main"` — standalone relay server

---

## 5. Async Model

Only the I/O boundary is async:
- `WebSocketRelayClient` / `RelayServer` (connect, subscribe, publish, sync)
- `SqliteStore` (wrapped in `asyncio.to_thread`)
- `GroupSession` (orchestration)
- `FakeRelay` (in-memory, async to match the Protocol)

Everything else (crypto, events, dag, state, completeness pure logic, chat handlers) is synchronous.

The CLI uses `asyncio.run()` at the top of each command. Tests use `pytest-asyncio` with `asyncio_mode = "auto"`.

The `WebSocketRelayClient` uses a single-reader model: a `_listen_loop` task reads all incoming messages. Push messages (`event`, `attestation`) are dispatched to callbacks via `asyncio.ensure_future`. Response messages (`receipt`, `not_found`, `sync_complete`, `ok`, `error`, `query_complete`) go to an `asyncio.Queue` where blocking request-response methods (`publish`, `get`, `request_attestation`, `submit_fraud_proof`, `sync`, `query_fraud_proofs`) consume them.

---

## 6. Testing Strategy

### 6.1 Active Tests (57 tests)

| Layer | Test File | Focus |
|---|---|---|
| Crypto | `tests/unit/crypto/test_crypto.py` | Key generation, signing, verification, hex encoding |
| Events | `tests/unit/events/test_events.py` | Canonical serialization, structural validation, signature verification |
| Events | `tests/unit/events/test_serialization_property.py` | Property-based: determinism, unicode round-trip, tag sorting |
| DAG | `tests/unit/dag/test_dag.py` | Head computation, gap detection, cycle check |
| State | `tests/unit/state/test_state.py` | State derivation, ban/unban/kick semantics, mod add/remove, metadata, `(ts,id)` ordering |
| Completeness | `tests/unit/completeness/test_completeness.py` | Receipt build/verify, attestation build/verify, `set_hash` determinism |
| Chat | `tests/unit/chat/test_chat.py` | Message/reaction/nickname builders |
| Integration | `tests/integration/test_fake_relay.py` | FakeRelay publish/get/sync/attestation round-trips |
| Integration | `tests/integration/test_event_roundtrip.py` | Build-sign-verify round-trip |
| Integration | `tests/integration/test_censorship_detection.py` | Attestation divergence detection, monitor pass detects missing-with-receipt |

### 6.2 Fixtures (`conftest.py`)

- `alice_keypair`, `bob_keypair`, `founder_identity`, `alice_identity`, `bob_identity` — deterministic Ed25519 keypairs (from fixed seeds)
- `group_keypair` — deterministic group keypair
- `sample_genesis` — a constructed genesis event for testing
- `memory_store` — an empty `MemoryStore`

### 6.3 Running Tests

```bash
pytest                      # all 57 tests
pytest -v                   # verbose
pytest --cov=fern --cov-report=term-missing  # coverage
```

Tests complete in ~0.1s. `hypothesis` is available for property-based tests but only used in the serialization property test currently.

---

## 7. Dependencies

### Runtime
- `cryptography >= 42.0` — Ed25519 sign/verify (Raw encoding)
- `websockets >= 12.0` — WebSocket client and server
- `click >= 8.1` — CLI argument parsing

### Dev
- `pytest`, `pytest-asyncio`, `pytest-cov` — testing
- `hypothesis` — property-based testing
- `ruff` — linting and formatting
- `mypy` — type checking (strict mode)
- `build` — for building wheels

---

## 8. Build / Packaging

```toml
[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[project]
name = "fern-protocol"
version = "0.1.0"
requires-python = ">=3.11"

[project.scripts]
fern = "cli.main:main"
fern-relay = "cli.relay_main:main"

[tool.setuptools.packages.find]
where = ["src", "."]
include = ["fern*", "cli*"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.mypy]
strict = true
files = ["src", "cli"]
```

Install for development: `pip install -e ".[dev]"`

---

## 9. Frontend Apps

### 9.1 Bracken (Web SPA)

`bracken/` is a single-page web application implementing the full FERN protocol client in the browser. It has **no backend** — all FERN logic runs in the browser:

- **Vite + React + TypeScript** — SPA framework
- **tweetnacl-js** — Ed25519 signing and verification
- **idb** — IndexedDB wrapper for local event/receipt/identity persistence
- **No REST API** — only WebSocket connections to FERN relays and a one-time HTTPS metadata fetch

Features: private-key identity create/import, group join with sync, real-time message list (connected-DAG filtering, auth filtering, mod action system messages, nickname display, jdenticon avatars, collapsed consecutive messages, retryable failed sends), profile popups with mod actions, mod-only slash commands, collapsible mobile sidebar, member/relay drawers, group info, relay count badge, and settings with nickname editing, private-key export, and logout.

Bracken implements the connected-DAG gate in TypeScript: disconnected events remain in IndexedDB for gap healing, but do not enter normal message rendering, group-state derivation, or future parent selection.

### 9.2 DAG Viewer

`cli/dag_viewer.py` serves an interactive DAG visualisation on `http://localhost:8760` using Python's stdlib `http.server`. Zero external dependencies — vis.js loaded from CDN in the browser. Features: interactive node graph with click-to-inspect, search/filter, legend, live updates via Server-Sent Events, works with any SQLite FERN store.

`cli/commands/dag.py` wraps it as a CLI command: `fern dag --db relay.db`.

### 9.3 Reusability for Other Apps

The existing `GroupSession` is the high-level API that any frontend uses. A web app (FastAPI, Starlette, etc.) would:
1. Import `fern` as a library
2. Instantiate `GroupSession` per user session
3. Expose HTTP/WS endpoints that translate to `GroupSession` calls
4. Forward live events via `session.on_event` callbacks to the frontend

A desktop app (PySide6, Textual, etc.) would do the same, running `GroupSession` in an asyncio event loop and binding UI updates to callbacks.

The library assumes no particular UI runtime — no `print()`, no `@app.route`, no global event loop.

---

## 10. Design Decisions (Quick Reference)

| Decision | Rationale |
|---|---|
| Lowercase hex everywhere | Case-mismatch in hashing breaks the protocol |
| Canonical serialization as load-bearing primitive | `id`, `sig`, receipt signing, attestation signing all depend on it |
| Frozen dataclasses | Prevent aliasing bugs, enable dict/set membership |
| Async at edge, sync in core | ~80% of code testable without event loop |
| Single-reader model for WebSocket client | Avoids race between request/response and pushed messages |
| `asyncio.to_thread` for SQLite | Prevents event-loop blocking |
| Author-local receipts, shared on-demand | Zero ongoing traffic; only published as fraud proof evidence |
| Fraud proofs not in DAG | Audit evidence, not group history |
| DAG for completeness propagation, not replies | Separate concerns; replies are `content.reply_to` |
| `connect_transports` shared helper | Single point for relay connection logic across CLI commands |
| Per-group SQLite cache | Fast reads after initial sync; durable across CLI invocations |
| Numbered groups (1, 2, 3...) | Simple UX; also accepts full pubkey |
