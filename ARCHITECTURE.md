# FERN Architecture

## Overview

FERN is a decentralized group messaging protocol. There is no server-side authority — identity is Ed25519 keypairs, all state is derived from a hash-linked DAG of signed events, and relays are interchangeable storage infrastructure.

The codebase has three kinds of component:

1. **Shared libraries** — protocol logic used by everything
2. **Relay server** — stores and forwards events
3. **Clients** — two implementations: a CLI client and a Qt GUI client

## Module Map

### Shared Libraries

These modules implement the protocol itself. Both clients and the relay server import from them.

| Module | Purpose |
|---|---|
| `crypto.py` | Ed25519 key generation, signing, verification, PEM key storage |
| `events.py` | Canonical serialization, event creation helpers, `GroupState` derivation, event verification |
| `dag.py` | `EventDAG` (local per-group event store + children index), `ClientStorage` (multi-group manager) |
| `sync.py` | Shared sync decision logic. `decide_sync_action()` — pure function that decides skip/incremental/full based on local DAG state and relay summaries. |
| `relay.py` | Plain async functions for relay communication. One-shot: `fetch_summary`, `fetch_events`, `publish`, `fetch_event`, `fetch_genesis`, `publish_to_all`. Persistent: `subscribe` (streams events until cancelled), `subscribe_with_retry` (auto-reconnects on disconnect). |
| `config.py` | Shared configuration values. `BOOTSTRAP_RELAYS` — default relay URLs used by CLI and debug tools. |
| `storage.py` | Resolves storage paths (`~/.fern`, `FERN_TEST_USER`, `--home`) |

### Relay Server

| Module | Purpose |
|---|---|
| `server.py` | WebSocket relay (`fern-server`). Stores events, serves sync/subscribe/publish/summary/get/get_genesis. No authority over group state. |

### Clients

| Module | Entry Point | Purpose |
|---|---|---|
| `client.py` | `fern` | CLI client. Full sync-and-heal, relay migration, event publishing. |
| `qt_chat/` | `fern-chat` | Qt GUI chat application (PyQt5). All UI code in `app.py`. |
| `inspect.py` | `fern-inspect` | DAG visualizer. Web UI showing real-time DAG rendering. |
| `debug.py` | `fern-debug` | Debug CLI. Verify events, dag-tree, state, gaps, compare-relays, health check. |
| `test.py` | `fern-test` | Test harness. Spawn users, multi-send, watch events. |

## Qt Chat Application (`qt_chat/`)

The Qt chat app is a PyQt5 desktop application. All UI code lives in `qt_chat/app.py`.

### Structure

```
qt_chat/
├── __init__.py     Entry point, creates QApplication and shows main window
├── app.py          All UI widgets, dialogs, stylesheet, and helpers
└── controller.py   Orchestration bridge between UI and background worker thread
```

### App.py Classes

| Class | Type | Purpose |
|---|---|---|
| `RETRO_STYLESHEET` | Constant | CSS stylesheet for retro Windows 95 look |
| `short_key()` | Helper | Truncates pubkey for display |
| `format_timestamp()` | Helper | Formats unix timestamps |
| `RetroTitleBar` | Widget | Gradient-painted custom title bar |
| `RelayStatusBar` | Widget | Shows relay connection status dots |
| `GroupListItem` | ListWidgetItem | Group list entry with icon, name, member count |
| `MemberItemWidget` | Widget | Member list entry with role icon and clickable pubkey |
| `ClickableLabel` | Label | Label that acts like a clickable link |
| `IdentityDialog` | Dialog | Shown on first launch to generate or import identity |
| `CreateGroupDialog` | Dialog | Form to create a new group |
| `JoinGroupDialog` | Dialog | Form to join via group address |
| `UserProfileDialog` | Dialog | Shows user info, role, and clickable pubkey |
| `GroupChatView` | Widget | Chat view for one group (messages, input, relay status) |
| `FernChatMain` | MainWindow | Main window with groups list, tabs, member list, event log |

### Controller Architecture

The Qt app uses a **controller pattern** to keep UI separate from protocol logic:

```
FernChatMain (UI)
    ↓ Qt signals
ChatController (Orchestration)
    ↓ pyqtSignals
RelayWorker (runs in QThread, async I/O)
    ↓
Relays (WebSocket)
```

- `ChatController` (in `controller.py`) runs in the main thread. It receives UI actions and routes them to the worker.
- `RelayWorker` (in `worker.py`) runs in a separate QThread. It handles all async WebSocket communication with relays.
- Results are emitted as Qt signals back to `FernChatMain` which updates the UI.

This separation keeps the UI responsive during network I/O.

### Key UI Patterns

**Splitter-based layout:** The main window uses `QSplitter` to allow users to resize panels (groups list, chat area, members panel).

**Retro styling:** The `RETRO_STYLESHEET` constant applies a Windows 95 aesthetic (3D beveled borders, gray backgrounds, navy accents) to all widgets.

**Event filtering:** `GroupChatView` uses an event filter on the message input to detect Enter key for sending.

**Max-height enforcement:** A splitter handles resizing the message input area, with a splitterMoved signal handler enforcing a 210px maximum.

## The Two Clients

`client.py` and `qt_chat/` are **independent programs** that implement the same protocol. They share `crypto.py`, `events.py`, and `dag.py`, but handle connections and sync differently.

### Connection Model

| | CLI (`client.py`) | Qt Chat (`qt_chat/`) |
|---|---|---|
| **Connections** | Short-lived. Opens a WebSocket per action, then closes. | Background `subscribe()` tasks stream events in a worker thread. |
| **Where sync logic lives** | `sync_and_heal()` — called inline before every action | `_smart_sync()` — called from controller signal handlers |
| **Relay healing** | Yes. Cross-references relays, pushes missing events. | No. |
| **Relay migration** | Yes. Follows migration chain across multiple sync rounds. | No. |
| **Signing** | Server-side Python. Keys on disk. | Server-side Python (same as CLI). |
| **Event storage** | `EventDAG` on disk via `ClientStorage` | Same `EventDAG` on disk via `ClientStorage` |

### Sync Logic

Both clients share the same decision function: `sync.py:decide_sync_action()`. It's a pure function (no I/O) that takes local DAG state and relay summaries, and returns a `SyncDecision` indicating skip, incremental, or full sync. Each client fetches summaries using its own connection machinery, calls this function, then executes the result.

The CLI has additional phases beyond the initial decision (relay discovery loops for migration, cross-relay healing). The Qt chat app does the initial decision and executes directly.

**`decide_sync_action` decision tree:**

1. No local events → `"full"` (since=0)
2. No relay summaries received → `"incremental"` (since = latest_ts - 60)
3. All relay tips exist in local DAG → `"skip"`
4. Relay has tips not in local DAG → `"incremental"` (since = latest_ts - 60)

Count is intentionally **not** compared, because relays store events that clients may reject (e.g. unauthorized mod actions). Tips are the correct signal — if the client knows all frontier events, there is nothing new to fetch.

The 60-second buffer (`CLOCK_SKEW_BUFFER`) handles late-arriving concurrent events with earlier timestamps.

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

## Entry Points

```
fern                CLI client (client.py)
fern-server         Relay server (server.py)
fern-chat           Qt chat app (qt_chat/__init__.py)
fern-inspect        DAG visualizer (inspect.py)
fern-debug          Debug tools (debug.py)
fern-test           Test harness (test.py)
```

All are installed via `pyproject.toml` console_scripts.
