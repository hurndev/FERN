# FERN-BFT Architecture

## Status and scope

This document describes the implementation on the `FERN-BFT` branch. FERN is
now a signed-event application replicated by one Tendermint-style consensus
chain per group. It is not a causal DAG, and validators are active consensus
participants rather than passive event forwarders.

The normative wire and validation rules are in [bft-spec.md](bft-spec.md).
[tendermint-design.md](tendermint-design.md) records the design that led to the
rewrite, while [BFT-NOTES.md](BFT-NOTES.md) records deliberate deviations and
unfinished work. The old DAG protocol remains in [spec.md](spec.md) only as
historical reference.

## 1. System overview

A FERN group is an independent replicated state machine. Users sign events,
send them to the group's validators, and may show them locally as pending after
receiving a validator's signed ingress receipt. A deterministic proposer orders
a batch, validators certify when they first observed every event in that batch,
and a Tendermint-style propose/prevote/precommit round finalizes a block.

Only a commit containing a quorum of precommits makes an event final. Final
order is `(height, position)`. There is no parent relation, head set, gap
healing, validator reconciliation, or client-selected conflict winner.

```text
                         one consensus domain per group

   fern CLI / Bracken
          |
          | signed event
          v
   +------------------ active validator set ------------------+
   | Validator A <---- signed peer messages ----> Validator B |
   |      ^                         ^                         ^ |
   |      +-------------------------+-------------------------+ |
   |              candidate -> proposal -> votes -> commit     |
   +-----------------------------------------------------------+
          |                         |
          +---- receipts/status ----+
          +---- commits/pending ----> independently verifying client

   Each validator: WebSocket server + multi-group node + SQLite store
```

The same validator process can host many groups, but each group has its own
genesis, application state, validator epoch, height, mempool, safety journal,
and consensus engine. A failure or validator-set change in one group does not
alter another group's consensus state.

## 2. Goals and non-goals

The current architecture aims to provide:

- permanent, independently verifiable final order;
- safety while at most `2q - n - 1` validators are Byzantine and liveness
  while at most `n - q` are unavailable, for a validator set of any size;
- deterministic authorization and state transitions;
- crash-safe vote and lock behavior;
- signed evidence for ingress, checkpoints, history hosting, and validator
  admission;
- roughly the same group-chat workflows in the CLI and Bracken as before the
  rewrite.

The implementation prioritizes safety over availability. It intentionally
does not provide an administrator override, owner recovery key, or automatic
fork choice if quorum is lost. It also does not yet solve deterministic fair
inclusion, authenticated peer channels, archival discovery, encrypted group
content, or validator compensation.

## 3. Identities and trust boundaries

FERN uses three distinct Ed25519 identities:

- A **user key** signs ordinary and boundary events. A user public key is the
  durable application identity.
- A **group key** signs only genesis. Its public key is the permanent group ID;
  it is not a validator key and does not override consensus after genesis.
- A **validator key** signs ingress receipts, candidates, timestamp
  observations, proposals, votes, statuses, manifests, hosting attestations,
  and readiness certificates.

Validator endpoints are committed in application state. Transport is currently
WebSocket over `ws://` or `wss://`; the peer connection itself is not mutually
authenticated. Safety therefore rests on validation of the signed, domain-
separated objects carried over it, not on connection identity. Operators
should use `wss://` on untrusted networks even though object signatures remain
mandatory.

The `operator` string attached to a validator is a display and local-policy
label. It is self-asserted and is not cryptographic proof that two validators
are independently controlled.

## 4. Canonical objects and events

All identifiers and signatures use compact UTF-8 canonical JSON. Object
signing payloads are domain-separated arrays; nested objects are recursively
key-sorted. Floats, non-finite values, and integers outside the JavaScript safe
integer range are rejected so Python and browser clients hash the same bytes.

An event has exactly these fields:

```json
{
  "protocol": "fern-bft-1",
  "id": "<sha256 hex>",
  "type": "chat.message",
  "group": "<group public key>",
  "author": "<user public key>",
  "seq": 1,
  "content": {},
  "ts": 1711234567,
  "tags": [],
  "sig": "<Ed25519 signature>"
}
```

The event ID is SHA-256 over:

```text
[protocol, type, group, author, seq, content, ts, tags]
```

The same payload is signed by `author`, except genesis is signed by the group
key. There are no DAG parents. Every non-genesis author has a strictly
increasing per-group sequence, and execution accepts only the next sequence.
Conflicting pending events at the same author sequence are rejected locally;
once one is finalized, every alternative is invalid.

The user-supplied `ts` field is provenance for display. It does not define
consensus order, authorization, ban expiration, or validator transitions.

## 5. Genesis and chain commitments

Genesis commits the core fields (chain ID, group metadata, founder,
public/private join policy, application namespace, and epoch-zero validator set)
plus the app's initial state — for chat, the channels and the initial managers
and mods. The group public key remains the addressable group identity.

Height zero is derived from genesis. Every later block commits to:

- group, chain ID, epoch, height, and round;
- previous block hash, previous state root, and previous history root;
- the full signed candidate and its timestamp evidence;
- certified times for the ordered events;
- the resulting application state root and cumulative history root.

The history root is a hash chain over finalized event IDs. `logical_bytes` is
the exact cumulative size of canonical genesis and commit bytes. Both values
appear in signed status, manifest, and readiness objects so a checkpoint
describes not just a height but the exact retained history.

Blocks contain at least one event: a bounded list of ordinary events and at
most one boundary event (carried in the block's governance slot). Ordinary
events execute in listed order; the boundary event executes last. This keeps a
membership, channel, ban, role, or validator-set change from retroactively
altering the validity of ordinary events earlier in the same block.

## 6. Validator sets and epochs

Validators are equal-weight, unique by key and URL, and sorted by public key.
A set may contain any number of validators from 1 to 100; the fault count and
quorum are derived from the validator count `n`:

```text
f = floor((n-1)/3)      quorum q = floor(2n/3) + 1
```

- Standard mode (`n >= 4`) commits with quorum `q`: the group tolerates
  `n - q` unavailable or Byzantine validators and stays fork-safe while at
  most `2q - n - 1` are Byzantine, halting rather than forking between those
  bounds. Sizes `4, 7, 10, …` (`3f+1`) have `q = 2f+1` with equal liveness
  and safety budgets; intermediate sizes add fork-safety margin without
  extra liveness.
- Unanimous small-set mode (`1 <= n < 4`) falls out of the same formula with
  `f = 0` and `q = n`: every validator must participate in every certificate.

Both modes use:

```text
propagation threshold   = f + 1
round catch-up threshold = f + 1
proposer index           = (height + round - 1) mod n
```

Any two quorums intersect in at least `2q - n` validators, so under the
safety bound their intersection contains an honest validator. Small-set mode
is a development and testing accommodation, not a claim that one to three
validators meet the standard Byzantine fault model; it offers no
unavailable-validator tolerance, and CLI and Bracken clients display a
persistent warning.

A `validator_update` is a boundary event whose authorization is delegated to
the app (in chat, only managers may trigger it). The old validator set
validates and commits the transition block. The new set becomes
active at the following height with its epoch incremented by one. The group
freezes safely if either active set cannot form a quorum; there is no
out-of-band membership recovery.

## 7. From ingress to finality

### 7.1 Ingress and pending display

An active validator verifies an event against the current finalized state and
the expected author sequence before persisting it in its mempool. The first
local acceptance time is stable. The validator returns a signed
`IngressReceipt` and gossips the user-signed event to peers.

A client can show one valid receipt as evidence that an event is pending.
Sending to all validators is preferred; `f+1` receipts prove that at least one
honest validator accepted the event under the configured fault assumption.
Receipts do not reserve a block position and are not consensus votes.

### 7.2 Candidate and certified observation time

For `(epoch,height,round)`, the deterministic proposer selects a bounded batch
from its persistent mempool. The signed candidate carries the complete events,
not just IDs. Each validator signs at most one timestamp-observation vector for
that round, only after receiving and validating the complete candidate. Every
entry is that validator's stable first-seen time for the corresponding event.

The proposer needs `q` valid observation vectors to build a block. The
integer median at each event position becomes its certified time. This proves
that a quorum possessed the entire batch and supplies Byzantine-resistant
approximate time for application policy. It does not change block order.

### 7.3 Proposal, votes, and commit

The proposer signs a proposal containing the complete block. Validators verify
all signatures, bounds, ancestry, timestamp evidence, and the entire
deterministic application transition before prevoting. Missing data, invalid
execution, or an insufficient valid-round proof causes a nil prevote.

Consensus then follows the Tendermint safety pattern:

1. A quorum of prevotes for a block allows a validator to lock it and
   precommit it.
2. A quorum of precommits for the same block forms a commit.
3. A validator precommits nil when the required block evidence is absent.
4. Timeouts move the engine to later rounds. Earlier catch-up requires `f+1`
   authenticated future-round senders. In unanimous small-set mode `f=0`, so
   one signed future-round sender is enough to resynchronize a restarted peer.

Each phase remains active until its own condition is met or its deadline
expires. Unrelated candidates, observations, or votes wake the engine only so
it can recheck that condition; their arrival alone never advances the phase.

Before transmission, a validator durably records its own vote. It durably
records the lock before emitting a block precommit. A validator never votes
for two values in the same phase and round, including nil versus non-nil.
Conflicting messages from another validator are retained as equivocation
evidence and that signer is excluded from the affected vote/observation count.

A later-round proposal may justify a locked value with a quorum of earlier
prevotes (`valid_round`). Without that proof an honest validator does not
unlock. Restart reloads the same votes and lock state from SQLite.

## 8. Deterministic application state

Every validator and client starts from genesis and executes committed events in
exactly the same order. State includes:

- chain ID, application namespace, and validator set;
- members currently known to the group and members currently joined;
- managers and mods (chat roles), and certified-time ban records;
- each author's last finalized sequence;
- group metadata, chat channels, and chat settings.

Core actions include join, leave, invite, kick, ban, unban, metadata changes,
and validator-set replacement. The chat app adds role changes (managers/mods),
channel administration, settings, and content (messages, reactions,
nicknames), and authorizes every event (see
[protocol-app-boundary.md](protocol-app-boundary.md)). Events whose namespace
is neither core nor the group's app are rejected; core and built-in chat events
receive full semantic validation.

Authorization is evaluated against the state immediately before an event.
Time-sensitive checks use certified median time, not the author's `ts` or the
local receiving clock. The resulting state is canonically serialized and
hashed; a proposal with the wrong root is invalid.

## 9. Persistence and crash safety

Each validator uses one SQLite database in WAL mode with full synchronous
durability. It stores hosted groups, commits, events, pending status, consensus
safety state, own votes, received votes, timestamp observations, equivocation
evidence, and local admission decisions.

Commit application is transactional: chain verification, event finalization,
application state, checkpoint roots, and height advance succeed together or
not at all. This prevents a crash from exposing a partially applied block.
Thread access is serialized, and event ingress holds a per-engine async lock so
concurrent submissions cannot both acquire the same author sequence.

Local admission records are operational policy, not consensus truth. A
validator may decide whether it has resources and trusted sources to host a
group, but once active it must apply the same on-chain rules as every peer.

## 10. Synchronization and split detection

Clients obtain genesis, request signed statuses from reachable validators, and
page complete commits from a chosen endpoint. Every commit is verified and
executed locally from the prior trusted checkpoint. A server response is never
accepted merely because it came from a configured validator URL.

Signed `ValidatorStatus` objects bind a validator to the group, chain ID,
height, block hash, history root, state root, epoch, validator set, and logical
byte count. Clients compare every status that can be placed on their locally
verified chain. Contradictory signed checkpoints are durable split evidence;
the client reports the conflict instead of selecting a branch.

History transfer currently pages up to 100 complete commits per request. It
does not yet use independent chunks or Merkle inclusion proofs. A signed
`HistoryManifest` fixes the target checkpoint, event/block counts, byte count,
and expiry so validator preparation cannot silently move to a different tail
while downloading.

Active validators also supervise their own committed height. On startup and
periodically while running, a lagging validator requires `f+1` distinct active
validator statuses ahead of its local checkpoint before pausing its group
engine. It then downloads and independently verifies every missing commit,
restarts the engine from the resulting durable state, reattaches subscriber
notifications, and resumes voting. Requiring `f+1` ahead reports prevents one
Byzantine validator from repeatedly pausing an otherwise current engine. This
is ordinary history catch-up, not catastrophic recovery: it cannot advance
without valid commit certificates and does not replace a lost quorum.

## 11. Safe validator admission

A prospective validator must fully synchronize and verify the group before it
can be added. Admission proceeds as follows:

1. Local policy selects trusted active validators and checks matching signed
   history-hosting evidence for one manifest.
2. The prospective validator downloads and verifies genesis and every commit
   through that fixed manifest checkpoint.
3. It persists the complete history and signs `SyncReady`, bound to the exact
   height, block hash, history root, logical byte count, source epoch, and next
   epoch.
4. An administrator includes readiness for exactly every newly added validator
   in `validator_update`.
5. The old set commits the transition. The prospective validator verifies that
   commit and starts an engine only if the resulting set activates its key.

Steps 1–3 may be initiated by operator action on the prospective validator
(`fern-validator prepare`) or remotely through the `request_readiness`
WebSocket action, which a client can invoke directly (Bracken's
`/validator-add <url>` does exactly this). Both paths apply the same local
admission policy; remote preparation is never a `manual` override. Clients
should retry once with fresh readiness if the group advances between
preparation and the update, because readiness is bound to the exact
pre-transition checkpoint.

Readiness must describe the block immediately before the transition. If the
chain advances before the update is proposed, preparation must be repeated.
This is intentionally strict: stale or approximate readiness cannot authorize
a validator that may lack the history needed to validate the next proposal.

## 12. Validator WebSocket boundary

A validator exposes one JSON request/response and subscription endpoint.
Current actions are:

- `metadata`, `bootstrap`, and `get_genesis`;
- `submit_event`, `get_event`, and `get_pending`;
- `status`, `get_commits`, and `subscribe`/`unsubscribe`;
- `history_manifest` and `hosting_attestation`;
- `request_readiness` for policy-gated remote validator preparation;
- `peer` for signed consensus and gossip messages.

`metadata` may include an optional signed `operator_notice` from the
validator operator and `peer_notices` — verified notices from other
validators in the set (see bft-spec.md §11). Both are non-authoritative
side-channel messages that never touch consensus.

Subscriptions push `pending_event` and `commit` messages. Request size and
per-action rate limits bound obvious ingress abuse. Genesis auto-hosting is a
development convenience and can be disabled with `fern-validator init --closed`.
Closed operation plus explicit preparation is recommended for untrusted
networks.

## 13. Client architecture

The Python CLI and Bracken are independent verifying clients. Both keep a
local chain cache, derive group state from verified commits, publish signed
events to configured validators, track ingress receipts, and separate pending
events from finalized events.

The CLI preserves familiar commands such as `fern group`, `fern post`, `fern
read`, `fern watch`, and `fern verify`. `fern chain` inspects verified head
state, validators/quorum, recent blocks and event positions, plus pending
events. It accepts a configured group ID for the normal sync-first workflow or
`--db` for direct cache/validator-database inspection. `fern dag` is a hidden
compatibility alias that reports its replacement. Validator-set replacement is
exposed as `validator-update`.

Bracken performs the same canonical hashing, Ed25519 checks, quorum checks,
state execution, epoch transitions, and split comparison in TypeScript. Its
IndexedDB v4 schema stores events, commits, ingress receipts, and validator
endpoint pins; upgrading
clears incompatible legacy DAG data. The old DAG viewer is replaced by a chain
viewer. Each active validator endpoint has an independent reconnect supervisor;
after a socket returns, Bracken synchronizes missed commits, verifies the
endpoint key against finalized state, and restores the subscription before the
UI reports it connected.

## 14. Failure and attack behavior

The central safety condition is that no more than `f` active validators in an
epoch violate the protocol and that honest validators retain their durable
vote/lock journals. Under that condition, two conflicting blocks cannot both
obtain a valid quorum commit at the same height.

Expected behavior is deliberately conservative:

| Condition | Result |
| --- | --- |
| Invalid or incomplete proposal | Honest validators prevote nil |
| Conflicting vote/observation from one signer | Evidence retained; signer excluded from that count |
| Fewer than `q` responsive validators | No commit; finalized history does not fork |
| Too few authenticated future-round senders | No forced catch-up |
| `f+1` validators report a future round (`1` in zero-fault small-set mode) | Engine may advance to that round |
| Conflicting signed checkpoints | Client reports a split and stops trusting automatic sync |
| Stale or incomplete `SyncReady` | Validator update is invalid |
| Malicious event timestamp | Display metadata only; policy uses certified time |

Consensus safety does not imply message availability or fair inclusion. A
Byzantine proposer can omit events during its rounds. Bounded queues, gossip,
and honest proposer rotation provide a practical liveness path after network
synchrony, but there is no proof that a proposer included every event known to
honest validators.

If more than `f` validators equivocate, lose their durable safety state, or sign
invalid history, the protocol's Byzantine safety guarantee no longer applies.
If a quorum is merely offline or partitioned, safety remains but liveness is
sacrificed.

## 15. Current implementation boundary

This branch is a reference implementation, not a production consensus stack.
The safety kernel is purpose-built rather than embedded from CometBFT. It is
isolated from networking and covered by adversarial tests, but it has not had
an external protocol audit.

Known remaining work includes deterministic inclusion policy, mutual peer
authentication, chunked archival transfer, durable public discovery,
protocol-level operator independence, capacity/retirement leases, and a tested
frontend build in an environment with Node.js. See [BFT-NOTES.md](BFT-NOTES.md)
for the exact implementation notes and validation constraints.
