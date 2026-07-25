# FERN-BFT Python Implementation Architecture

## Status

This document describes the Python implementation on the `FERN-BFT` branch.
It complements the protocol-level [architecture.md](architecture.md) and the
normative [bft-spec.md](bft-spec.md). Paths and responsibilities below refer to
the current source tree, not the removed DAG, completeness, healing, or generic
transport packages.

## 1. Design principles

### 1.1 Determinism before I/O

Canonical encoding, signed value objects, commit verification, and application
execution are synchronous and deterministic. They accept explicit inputs and
do not read clocks, files, sockets, or global process state. The validator
runtime supplies time and persistence at their boundaries.

This split lets the same block be checked by a live validator, a synchronizing
client, a CLI verification command, and the TypeScript implementation without
replaying network behavior.

### 1.2 Safety state is durable state

Votes and locks are not transient runtime details. `ConsensusCore` writes its
own vote before returning it to the caller and writes a new lock before
returning a block precommit. `BFTStore` rejects a conflicting vote even after a
restart. Network code cannot bypass these operations.

### 1.3 Async orchestration at the edge

WebSocket handling, peer broadcasts, consensus timers, subscriptions, and
multi-validator requests use `asyncio`. Cryptography and state transitions
remain ordinary synchronous calls. SQLite access is protected by a reentrant
thread lock; each group engine additionally serializes concurrent event ingress
with an async lock.

### 1.4 Immutable protocol values

Signed protocol objects and application snapshots are frozen dataclasses.
Transitions construct replacements instead of mutating objects after their
hashes or signatures have been checked. JSON parsing is strict at object
boundaries so malformed values do not leak into the core.

### 1.5 Conservative validation

Unknown evidence, missing proposal data, mismatched roots, equivocation, and
incomplete readiness fail closed. The implementation does not infer a recovery
fork or silently repair consensus history. Liveness policy is kept outside the
small voting and locking kernel.

## 2. Dependency shape

The intended dependency direction is:

```text
fern.crypto
    ^
    |
fern.events -----> fern.identity / fern.chat
    ^
    |
fern.bft canonical values, validators, certificates, blocks
    ^
    |
fern.bft application + chain + consensus
    ^
    |
fern.bft store + node + websocket + client + admission
    ^
    |
cli commands / fern-validator process
```

`consensus.py` knows how to vote safely but does not open sockets, wait on
timeouts, select mempool events, or apply SQLite commits. `node.py` owns those
runtime concerns. `websocket.py` adapts the node and store to the wire API.

The `fern.validator` package holds process configuration and network rate
limiting. The old `fern-relay` executable remains only as a warning-emitting
migration alias for existing installations.

## 3. Source tree

```text
src/fern/
├── bft/
│   ├── admission.py       prospective-validator history preparation
│   ├── application.py     deterministic replicated application state
│   ├── blocks.py          candidate, block, proposal, and commit objects
│   ├── canonical.py       cross-runtime canonical JSON, hashes, signatures
│   ├── certificates.py    ingress, observation, vote, and readiness objects
│   ├── chain.py           commit and full-chain verification
│   ├── client.py          verified sync and multi-validator publication
│   ├── consensus.py       crash-safe voting and locking kernel
│   ├── constants.py       protocol version, phases, and size limits
│   ├── manifest.py        manifests, hosting evidence, local admission
│   ├── node.py            per-group engine and multi-group validator node
│   ├── store.py           SQLite history, mempool, and safety journal
│   └── validators.py      validator and quorum rules
├── chat/                  event-content helpers for the built-in app
├── crypto/                Ed25519, hashes, and key encoding
├── events/                event model, building, serialization, validation
├── identity/              user and group key wrappers
├── validator/
│   ├── config.py          validator process configuration and keys
│   └── rate_limiter.py    per-action network rate limiting
└── errors.py

cli/
├── bft.py                 shared client sync/build/publish helpers
├── config.py              identity, group addresses, and local paths
├── logging_config.py      shared concise/verbose console formatting
├── main.py                `fern` command registration
├── validator_main.py      `fern-validator` process and preparation commands
└── commands/              group and chat commands

tests/
├── bft/                   application, protocol, store, runtime, wire, epochs
└── unit/crypto/           retained primitive coverage
```

The old `fern.client`, `fern.completeness`, `fern.dag`, `fern.state`,
`fern.storage`, and `fern.transport` packages were removed. Their abstractions
encoded assumptions about loose DAG events and server reconciliation that do not
fit committed block history.

## 4. Pure protocol layers

### 4.1 `fern.crypto`

This package wraps Ed25519 keys and signatures, SHA-256 hashing, hex encoding,
and random key/channel material. Protocol code exposes lowercase hex strings
on the wire. Private keys stay in identity or validator configuration objects.

### 4.2 `fern.events`

`Event` is the shared signed application envelope. The BFT rewrite retains the
familiar content model while changing the signed structure to:

```text
[protocol, type, group, author, seq, content, ts, tags]
```

`build.py` constructs and signs user or genesis events. `serialization.py` and
`validation.py` enforce exact fields, size limits, canonical IDs, signatures,
and sequence shape. `semantic.py` validates known protocol and chat content.
`types.py` centralizes event type constants and identifies governance/state
events.

Event validation here is context-free. Membership, authorization, expected
sequence, channel existence, and certified-time bans belong to
`bft.application` because they depend on a particular finalized state.

### 4.3 `bft.canonical`

`canonical.py` is the common serialization boundary for every BFT object. It:

- recursively normalizes JSON values;
- sorts object keys by Unicode code point;
- emits compact UTF-8 JSON;
- rejects booleans where an integer is required, floats, and unsafe integers;
- produces SHA-256 IDs and Ed25519 signatures for domain payloads.

The matching TypeScript functions in Bracken must remain byte-for-byte
compatible whenever an object schema changes.

### 4.4 `bft.validators`

`Validator` validates a public key, `ws://` or `wss://` URL, and bounded
operator label. `ValidatorSet` enforces sorted distinct keys and URLs, the
maximum of 100 validators, and requires the committed `fault_tolerance` to
equal the count-derived `f = (n-1)//3`. The quorum is `floor(2n/3) + 1`,
which equals `n` (unanimous) for one to three validators and `2f+1` for
sizes `3f+1`.

It exposes the derived `quorum`, ingress `propagation_threshold = f+1`, and
`round_catchup_threshold = f+1` (both computed as `n - q + 1`). In unanimous
small-set mode the derived fault count is zero, so one authenticated
future-round sender can resynchronize
a restarted validator. It also provides membership checks and deterministic
proposer selection. Validator-set construction sorts by public key so input
order cannot change the proposer schedule. The CLI and Bracken surface a
warning whenever verified state uses the unanimous small-set mode.

### 4.5 `bft.certificates`

This module defines and validates four signed evidence types:

- `IngressReceipt`: an active validator accepted an event at a stable local
  first-seen time;
- `TimestampObservation`: one validator's observation vector for one candidate
  and consensus position;
- `Vote`: a prevote or precommit for a block ID or nil;
- `SyncReady`: a prospective validator has the exact verified history needed
  for an epoch transition.

All are bound to the relevant group, chain ID, epoch, and consensus/checkpoint
coordinates. Constructors do not make an object trusted; callers must use the
corresponding verification function and active validator set.

### 4.6 `bft.blocks`

The block layer is split into progressively stronger objects:

- `Candidate` is the proposer's signed, complete event batch.
- `Block` adds quorum timestamp observations, medians, ancestry, state root,
  and history root.
- `Proposal` binds the block to the current consensus round and optionally
  carries a quorum valid-round proof.
- `Commit` pairs a block with a quorum of matching precommits.

`build_block` calculates medians and roots. Verification functions re-check
signers, quorums, exact object IDs, size/event bounds, and evidence alignment.
They do not mutate application state.

### 4.7 `bft.application`

`ApplicationState` is an immutable snapshot of all consensus-controlled group
state. `initial_state_from_genesis` validates and derives height-zero state.
`validate_event_for_state`, `apply_event`, and `execute_events` implement
authorization and deterministic state transitions.

`execute_events` enforces the ordinary-events-first, single-governance-last
rule. Validator updates validate readiness against the incoming checkpoint and
produce the next epoch's complete set. The `ApplicationState.root` property
hashes canonical serialized state and is checked against each proposed block.

The module never calls `time.time()`. The caller supplies certified event times
and the checkpoint values used to validate readiness.

### 4.8 `bft.chain`

`verify_and_apply_commit` is the main reusable verification function. Given a
trusted `ChainHead` and one `Commit`, it checks:

- exact next height, active epoch, and parent commitments;
- proposal and precommit evidence under the incoming validator set;
- timestamp evidence and certified medians;
- deterministic application execution;
- state, history, block, and logical-byte commitments.

It returns a new `ChainHead` or raises `ChainVerificationError` without changing
storage. `verify_chain` folds the same operation over commits starting at
genesis. Validators and clients therefore share the same validity path.

### 4.9 `bft.consensus`

`ConsensusCore` is the small safety-critical state machine for one validator at
one height. It handles round entry, prevote selection, valid-round unlocking,
locking after a prevote quorum, nil precommits, and durable own-vote creation.

`VoteSet` validates and counts votes for a single group/chain/epoch/height/
round/phase. Equivocating validators are excluded rather than allowed to count
toward competing values. `SafetyState` contains the current round, locked
round/block/proposal, and most recent valid proposal.

Persistence is abstracted by the narrow `SafetyJournal` protocol.
`MemorySafetyJournal` supports focused tests; `BFTStore` is the production
implementation. Networking and timeout behavior deliberately do not appear in
this module.

## 5. Persistence and runtime layers

### 5.1 `bft.store`

`BFTStore` owns the SQLite connection and implements `SafetyJournal`. It enables
WAL, `synchronous=FULL`, and foreign keys. The schema contains:

- `bft_groups` for genesis, application state, and the current checkpoint;
- `bft_commits` for finalized blocks;
- `bft_events` for pending/finalized events and certified positions;
- `bft_safety_state` and `bft_own_votes` for crash safety;
- `bft_votes`, `bft_observations`, and `bft_own_observations` for live rounds;
- `bft_equivocations` for conflicting signed evidence;
- `bft_admissions` for local hosting policy.

`save_commit` calls the shared chain verifier and applies all history and state
changes in one `BEGIN IMMEDIATE` transaction. The store also implements stable
first-seen times, pending author-sequence allocation, deterministic mempool
selection, commit paging, and finalized event queries.

One `BFTStore` can host many independent groups. Callers should close it
explicitly; CLI commands use `try/finally` around each local cache.

### 5.2 `bft.node`

`GroupEngine` is one validator's async runtime for one group. It owns timers,
round-local candidate/proposal caches, the `ConsensusCore`, gossip reactions,
and commit/pending listeners. Its responsibilities include:

- accepting and gossiping events without concurrent sequence races;
- selecting candidates when the local key is proposer;
- collecting timestamp observations and building proposals;
- validating incoming candidates, proposals, votes, and commits;
- moving through propose, prevote, precommit, timeout, and later rounds;
- catching up after the validator set's mode-dependent future-round threshold;
- persisting commits and starting the next height.

`ConsensusTiming` makes block interval, phase timeouts, and round backoff
explicit and replaceable in tests.
The runtime waits through arbitrary inbound traffic until the active phase's
specific condition is satisfied or its timeout expires; one partial
observation or vote never acts as an implicit timeout.

`ValidatorNode` owns one validator key, one store, and a `GroupEngine` per hosted
group in which that key is active. It routes peer messages and starts/stops all
engines. A fully synchronized prospective validator remains inactive; it may
accept exactly the next verified transition commit and starts an engine only
if that commit activates its key.

### 5.3 `bft.websocket`

`ValidatorServer` adapts a `ValidatorNode` to the WebSocket JSON API. It handles
metadata, genesis bootstrap, peer messages, event submission, status and
history queries, manifests, hosting attestations, pending lookup, remote
validator preparation, and subscriptions. It pushes pending events and commits
to subscribed clients and enforces message-size and per-action rate limits.
The `request_readiness` action runs the same policy-gated admission path as
the CLI `prepare` command (trusted-host threshold, byte budget, never
`manual`) under the node's per-group lock, so the periodic catch-up path
cannot race the admission sync; it is rate-limited more strictly than
ordinary queries because it triggers a full verified history download.

`PeerBroadcaster` sends signed node messages to the active set's configured
URLs. `BFTWebSocketClient` is the low-level request helper used by the CLI,
admission flow, and client synchronization. `ValidatorStatus` is defined here
because it is the signed checkpoint representation exposed by the server.

The wire adapter parses untrusted JSON into strict domain objects before
passing it inward. Handler errors become error responses rather than escaping
the connection task.

### 5.4 `bft.client`

This module provides application-independent verifying client workflows:

- `sync_from_validators` fetches statuses, genesis, and commits, verifies from
  the local head, and checks comparable signed checkpoints for splits;
- `sync_to_manifest` downloads through one fixed, signed history target for
  prospective-validator preparation;
- `publish_to_validators` submits to multiple validators and verifies ingress
  receipts.

`SyncResult` and `PublishResult` return evidence and partial endpoint failures
to the caller. One unavailable validator does not erase successful signed
responses, but contradictory evidence is a verification error.

### 5.5 `bft.manifest` and `bft.admission`

`manifest.py` defines signed `HistoryManifest` and `HostingAttestation` values,
their verification, the independent-operator threshold check, and the local
`AdmissionRecord`.

`prepare_validator_history` orchestrates the safe admission path. It selects a
matching manifest and hosting evidence using local trust configuration,
downloads and verifies the fixed history, persists the admission decision, and
returns a signed `SyncReady` for the exact checkpoint. Manual preparation is an
explicit local resource-policy override; it never bypasses chain verification
or on-chain readiness validation. The function is invoked both by the CLI
`prepare` command and by the server's `request_readiness` action, so local and
remote preparation share one implementation and one policy gate.

## 6. CLI architecture

Two console scripts are registered by `pyproject.toml`:

```text
fern        -> cli.main:main
fern-validator -> cli.validator_main:main
fern-relay     -> warning-emitting compatibility entry point
```

`cli.config` stores the user key, group order, validator URLs, and per-group
cache paths below `FERN_HOME` (default `~/.fern`). A group address has the form:

```text
fern:<group-public-key>@ws[s]://validator-a,...
```

The address supplies discovery endpoints. After verified sync, the validator
set in finalized application state is authoritative and the local config is
updated to match it.

`cli.bft` centralizes the common flow used by commands: open a `BFTStore`, sync
and verify the group, derive the next user sequence, build a signed event, and
publish it to the current validator set.

The user-facing command families are:

| Command | Responsibility |
| --- | --- |
| `fern init`, `fern whoami` | User identity |
| `fern group create/join/list/info/members` | Group lifecycle and verified state |
| `fern group leave/invite/kick/ban/unban` | Membership governance |
| `fern group admin-add/admin-remove/nickname` | Administration and profile events |
| `fern group validator-update` | Full validator-set replacement with readiness |
| `fern post/read/watch` | Publish, inspect, and subscribe to chat history |
| `fern verify` | Re-verify the full cached chain from genesis |
| `fern chain [group] [--db ...]` | Inspect head state, validators, blocks, finalized event positions, and pending events |
| `fern validator init/start/info` | Local validator operation |

`fern dag --db ...` remains a hidden alias for `fern chain --db ...` and emits
a rename warning. Hidden legacy options such as `--no-heal` and
`--show-rejected` are accepted for script compatibility but have no DAG-healing
meaning.

The top-level `fern` Click group keeps only lightweight command names and help
text in memory. It imports a command module—and therefore the BFT, WebSocket,
SQLite, and cryptography stacks—only after that command is selected. Top-level
help does not import any `fern.bft` module, which keeps menu startup fast even
when the editable checkout resides on network storage.

`fern-validator` uses `FERN_VALIDATOR_HOME` (default `~/.fern-validator`) and
provides:

- `init` to create a validator key, config, and SQLite path;
- `run` to start the multi-group validator and WebSocket server;
- `config show/add-witness/remove-witness` for local admission trust;
- `prepare` to stage verified history and write a `SyncReady` JSON file.

The `witness` command wording is retained for configuration compatibility; it
now identifies a trusted validator source and local operator label.

Both console scripts use structured standard-library logging. Validator INFO
records cover server/group lifecycle and finalized blocks. `fern-validator
--verbose` or `fern-validator run --verbose` enables DEBUG records at consensus
transition points: event acceptance, round stages, candidate/proposal creation,
local vote decisions, quorum formation, future-round catch-up, and advancement.
Individual inbound votes and complete event content are intentionally omitted.

`fern --verbose <command>` enables client DEBUG records for validator
discovery, signed status comparison, manifest/source choice, commit pages,
verified blocks, publication results, and ingress receipts. The WebSocket
library's frame-level logger stays at WARNING so application records remain
readable.

## 7. Bracken boundary

Bracken is a separate browser implementation, not a thin view over Python. The
important matching modules are:

```text
bracken/src/fern/events.ts   exact fern-bft-1 event encoding and signatures
bracken/src/fern/bft.ts      validator, proposal, commit, and chain checks
bracken/src/fern/state.ts    deterministic application execution and state root
bracken/src/fern/validator.ts WebSocket requests, receipts, status, subscription
bracken/src/fern/db.ts       IndexedDB v4 events, commits, receipts, endpoint pins
bracken/src/fern/logger.ts   structured browser lifecycle and debug logging
bracken/src/hooks/useBracken.ts
                             sync, publish, reconnect, split checks, and UI state
```

The React layer consumes derived pending/finalized events and chain metadata.
For the active group, one abortable supervisor per validator maintains the
WebSocket subscription. Reconnect uses bounded exponential backoff and performs
a verified catch-up sync before changing the endpoint back to connected.
`ChainViewer.tsx` replaces the old graph visualization. IndexedDB upgrading to
v3 clears legacy event objects because DAG event IDs and state are incompatible
with `fern-bft-1`.

Bracken emits normal connection, synchronization, ingress, and finalized-block
records to the developer console. Request-level DEBUG records are enabled in
Vite development builds or when the `fern:verbose` local-storage key is
`"true"`. Logs use shortened public identifiers and never include event content,
signing seeds, or private keys.

Python changes to canonical encoding, block fields, state serialization, or
epoch execution require an equivalent TypeScript change. Cross-runtime test
vectors are a useful future addition; currently both implementations encode
the same rules directly.

## 8. Testing and verification

The active Python tests are organized by architectural boundary:

- `test_protocol.py`: canonical objects, signatures, commits, tamper rejection,
  quorum intersection, locks, nil votes, restart safety, and equivocation;
- `test_application.py`: deterministic state and certified policy behavior;
- `test_store.py`: persistence, transactions, and concurrent ingress support;
- `test_runtime.py`: four-validator rounds, gossip, missed candidate messages,
  and commit progress;
- `test_websocket.py`: server/client sync, subscriptions, and admission;
- `test_epochs.py`: readiness, governance ordering, and epoch handoff.
- `test_logging.py`: CLI verbose controls and real consensus lifecycle logs.

Run the maintained checks from the repository root:

```bash
ruff check src cli tests
mypy src cli
pytest -q
```

Frontend checks require a Node.js toolchain:

```bash
cd bracken
npm run lint
npm run build
```

Tests use `MemorySafetyJournal` for isolated consensus rules, temporary SQLite
stores for crash and transaction behavior, short `ConsensusTiming` values for
runtime tests, and real loopback WebSockets for boundary coverage. Safety tests
should assert refusal behavior as well as successful commits.

## 9. Dependencies and packaging

Python 3.11 or newer is required. Runtime dependencies are intentionally
small:

- `cryptography` for Ed25519;
- `websockets` for client, peer, and server transport;
- `click` for both CLIs;
- the standard library for SQLite, asyncio, JSON, hashing, and configuration.

Development dependencies include pytest/pytest-asyncio, Hypothesis, Ruff,
Mypy, coverage, and build tooling. Mypy is configured in strict mode for
`src` and `cli`. Setuptools discovers both `fern*` and `cli*` packages.

## 10. Change checklist

When changing a signed object or consensus rule:

1. Update the concrete rule in `bft-spec.md`.
2. Change parsing and canonical payload code together.
3. Verify that every signature is bound to group, chain, epoch, height, round,
   and phase where applicable.
4. Keep `ConsensusCore` independent of networking and timers.
5. Persist any new vote/lock safety fact before a signed message can escape.
6. Apply equivalent canonical, verification, and state changes in Bracken.
7. Add rejection tests for malformed, stale, conflicting, and restart cases.
8. Run Python checks and the frontend build when its toolchain is available.

When changing only operational policy, keep it out of canonical application
state unless every validator and client must agree on it. Local trust labels,
resource admission, timeouts, and rate limits are examples of local policy;
validator membership, authorization, sequence numbers, and roots are consensus
state.
