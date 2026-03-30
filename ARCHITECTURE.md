# FERN Architecture

## Overview

FERN is a decentralized group messaging protocol. There is no server-side authority — identity is Ed25519 keypairs, all state is derived from a hash-linked DAG of signed events, and relays are interchangeable storage infrastructure.

The codebase has three kinds of component:

1. **Shared libraries** — protocol logic used by everything
2. **Relay server** — stores and forwards events
3. **Clients** — two independent implementations that talk to relays

## Module Map

### Shared Libraries

These modules implement the protocol itself. Both clients and the relay server import from them.

| Module | Purpose |
|---|---|
| `crypto.py` | Ed25519 key generation, signing, verification, PEM key storage |
| `events.py` | Canonical serialization, event creation helpers, `GroupState` derivation, event verification |
| `dag.py` | `EventDAG` (local per-group event store + children index), `ClientStorage` (multi-group manager) |
| `sync.py` | Shared sync decision logic. `decide_sync_action()` — pure function that decides skip/incremental/full based on local DAG state and relay summaries. Used by both `client.py` and `chat.py`. |
| `relay.py` | Unified WebSocket client for FERN protocol. `RelayClient` class with one-shot classmethods for CLI and persistent instance connections for chat. |
| `config.py` | Shared configuration values. `BOOTSTRAP_RELAYS` — default relay URLs used by CLI, debug, and test tools. |
| `storage.py` | Resolves storage paths (`~/.fern`, `FERN_TEST_USER`, `--home`) |

### Relay Server

| Module | Purpose |
|---|---|
| `server.py` | WebSocket relay (`fern-server`). Stores events, serves sync/subscribe/publish/summary/get/get_genesis. No authority over group state. |

### Clients

| Module | Entry Point | Purpose |
|---|---|---|
| `client.py` | `fern` | CLI client. Full sync-and-heal, relay migration, event publishing. |
| `chat.py` | `fern-chat` | Web chat app. Browser does signing via JS; Python backend proxies to relays. |
| `inspect.py` | `fern-inspect` | DAG visualizer. Web UI showing real-time DAG rendering. |
| `debug.py` | `fern-debug` | Debug CLI. Verify events, dag-tree, state, gaps, compare-relays, health check. |
| `test.py` | `fern-test` | Test harness. Spawn users, multi-send, watch events. |

### Frontend

| Path | Purpose |
|---|---|
| `static/chat.html` | Single-file web chat UI. Client-side Ed25519 signing, event creation, state derivation. |

## The Two Clients

`client.py` and `chat.py` are **independent programs** that implement the same protocol. They share `crypto.py`, `events.py`, and `dag.py`, but handle connections and sync differently.

### Connection Model

| | CLI (`client.py`) | Chat (`chat.py`) |
|---|---|---|
| **Connections** | Short-lived. Opens a WebSocket per action, then closes. | Persistent. `RelayClient` objects stay connected via `ChatSession`. |
| **Where sync logic lives** | `sync_and_heal()` — called inline before every action | `_smart_sync()` — called from `ChatSession.handle_message` when browser sends `{action: "sync"}` |
| **Relay healing** | Yes. Cross-references relays, pushes missing events. | No. |
| **Relay migration** | Yes. Follows migration chain across multiple sync rounds. | No. |
| **Signing** | Server-side Python. Keys on disk. | Client-side browser JS. Keys in localStorage (fetched from backend on first load). |
| **Event storage** | `EventDAG` on disk via `ClientStorage` | Same `EventDAG` on disk (backend), plus browser-side array in memory |

### Sync Logic

Both clients share the same decision function: `sync.py:decide_sync_action()`. It's a pure function (no I/O) that takes local DAG state and relay summaries, and returns a `SyncDecision` indicating skip, full, or incremental sync. Each client fetches summaries using its own connection machinery, calls this function, then executes the result.

The CLI has additional phases beyond the initial decision (relay discovery loops for migration, cross-relay healing). The chat only does the initial decision and executes it directly.

**`decide_sync_action` decision tree:**

1. No local events → `"full"` (since=0)
2. No relay summaries received → `"incremental"` (since = latest_ts - 60)
3. All relay tips exist in local DAG → `"skip"`
4. Relay has tips not in local DAG → `"incremental"` (since = latest_ts - 60)

Count is intentionally **not** compared, because relays store events that clients may reject (e.g. unauthorized mod actions). Tips are the correct signal — if the client knows all frontier events, there is nothing new to fetch.

The 60-second buffer (`CLOCK_SKEW_BUFFER`) handles late-arriving concurrent events with earlier timestamps.

### Data Flow in Chat

```
Browser                          chat.py (Python)                    Relay
  │                                   │                                │
  │  {action: "load_local", group}    │                                │
  │ ─────────────────────────────────>│                                │
  │  events from local EventDAG       │                                │
  │ <─────────────────────────────────│                                │
  │                                   │                                │
  │  {action: "connect_relay", ...}   │                                │
  │ ─────────────────────────────────>│──── connect ─────────────────>│
  │                                   │<─── connected ────────────────│
  │                                   │                                │
  │  {action: "sync"}                 │                                │
  │ ─────────────────────────────────>│                                │
  │                                   │  _smart_sync():                │
  │                                   │    check local DAG             │
  │                                   │    fetch summaries             │
  │                                   │    decide: skip/incr/full      │
  │                                   │──── sync(since) ──────────────>│
  │                                   │<─── events + sync_complete ───│
  │  events forwarded to browser      │                                │
  │ <─────────────────────────────────│                                │
  │  {type: "sync_complete"}          │                                │
  │ <─────────────────────────────────│                                │
  │                                   │                                │
  │  {action: "publish", event}       │                                │
  │ ─────────────────────────────────>│──── publish ──────────────────>│
  │                                   │<─── ok/error ─────────────────│
  │  {type: "ok"} or {type: "error"}  │                                │
  │ <─────────────────────────────────│                                │
```

Key point: the browser creates and signs events itself (using `@noble/ed25519`). The Python backend verifies them, forwards to relays, and stores locally.

## Relay Server

The relay (`server.py`) is deliberately dumb:

- Stores events with valid signatures, no auth checks
- Serves events via sync (timestamp-filtered), subscribe (live push), get (by ID), summary (count + tips)
- Does not derive group state, does not enforce authorization rules
- GC rule: may delete unreferenced tip events after N subsequent events (configurable, default 100)

## Event Types and State Derivation

All 10 event types and state derivation rules are in `events.py` (`GroupState.apply()`). Both clients and any future tooling must use the same derivation to agree on group state.

Events are sorted by `(ts, id)` — timestamp first, then event ID as tiebreaker (lexicographic). Conflict resolution: when two events have the same timestamp and affect the same state, the one with the lexicographically greater ID wins.

## Storage Layout

```
~/.fern/                          (or /tmp/<user>/.fern for FERN_TEST_USER)
├── keys/
│   ├── user.pem                  (Ed25519 user identity)
│   └── group_default.pem         (group private key, used at creation only)
└── groups/
    └── <group_pubkey>.json       (event array for one group)
```

## Maintenance Notes

### Sync logic divergence

The summary-check + incremental-sync decision logic lives in `sync.py:decide_sync_action()`. Both `client.py` and `chat.py` call this shared function — the decision logic cannot diverge. Each client still executes the decision differently (short-lived connections vs persistent), but the "skip vs incremental vs full" logic is shared.

### Chat browser state

The browser maintains its own `events` array separate from the backend's `EventDAG`. The backend is the source of truth for local storage. The browser's array is ephemeral — it's populated by `load_local` and relay events, and cleared when switching groups.

### RelayClient queuing

`RelayClient` in `chat.py` queues sync requests if the connection isn't established yet. When the connection completes, queued syncs are drained. This replaced a fragile `setTimeout(100ms)` that the browser used to guess when connections were ready.

### Where signing happens

- CLI: signing happens in Python (`events.py` helpers called from `client.py`)
- Chat: signing happens in the browser (`chat.html` using `@noble/ed25519`). The backend only verifies and forwards.
- Both produce identical events (same canonical serialization, same Ed25519 signatures).

## Entry Points

```
fern                CLI client (client.py)
fern-server         Relay server (server.py)
fern-chat           Web chat app (chat.py)
fern-inspect        DAG visualizer (inspect.py)
fern-debug          Debug tools (debug.py)
fern-test           Test harness (test.py)
```

All are installed via `pyproject.toml` console_scripts.
