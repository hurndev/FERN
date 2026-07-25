# FERN-BFT Protocol Specification (Implementation Draft)

## Status

This document is the executable protocol contract for the `FERN-BFT` branch.
It turns the architecture in `tendermint-design.md` into concrete objects and
validation rules. It is intentionally conservative: ambiguous or incomplete
evidence stops consensus instead of permitting a validator to guess.

The protocol identifier is `fern-bft-1`. It is not wire-compatible with the
legacy DAG protocol in `spec.md`.

## 1. Cryptography and canonical encoding

FERN-BFT uses Ed25519 signatures and SHA-256 identifiers. Keys, signatures and
hashes are lowercase hexadecimal strings. Domain objects are signed over a
compact UTF-8 JSON array whose field order is specified for that object.
Objects nested inside a signing payload use recursively key-sorted JSON
objects. There is no insignificant whitespace and no trailing newline.
Numbers are signed safe integers in the range common to Python and JavaScript;
floating point, non-finite and out-of-range numeric values are invalid.

Every signing payload starts with a distinct type or protocol domain. A
signature for an event, observation or vote is therefore not reusable as a
different object. Consensus objects are additionally bound to the group,
chain ID, validator epoch, height, round and phase wherever those fields are
meaningful.

## 2. User events

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

The canonical event payload is:

```text
[protocol, type, group, author, seq, content, ts, tags]
```

`id` is the SHA-256 of that payload. `sig` is the author's signature over the
same payload, except that a `genesis` event is signed by the group key. `ts` is
author-supplied display metadata only. It never controls authorization,
expiration, consensus order or validator transitions.

There are no DAG parents. Every non-genesis author uses a strictly increasing
per-group sequence beginning at one. A finalized event is valid only when its
sequence is exactly one greater than the author's last finalized sequence.
This rejects replay while permitting a client to prepare consecutive pending
events.

The maximum encoded event size is 32 KiB. Tags retain the legacy bounds and
are reserved for extensions. Event content schemas and chat limits remain the
same unless this document says otherwise.

## 3. Genesis and group identity

The group public key remains the permanent group identifier. Its private key
signs one genesis event and is not a consensus validator key.

Genesis uses `seq = 0` and contains at least:

```json
{
  "chain_id": "<random 32-byte hex>",
  "name": "Group name",
  "description": "",
  "public": true,
  "founder": "<user public key>",
  "admins": ["<user public key>"],
  "validators": [
    {
      "pubkey": "<validator public key>",
      "url": "wss://validator.example/",
      "operator": "independent operator label"
    }
  ],
  "fault_tolerance": 1,
  "app": "chat"
}
```

Validators are equal-weight and sorted by public key. A set may contain any
number of validators from 1 to 100. The fault count `f` and quorum `q` are
derived from the validator count `n`:

```text
f = floor((n-1)/3)
q = floor(2n/3) + 1
```

The committed `fault_tolerance` field must equal the derived `f`. For
`n >= 4` (standard mode) the group keeps committing while at least `q`
validators are online and honest — tolerating `n - q` unavailable or
Byzantine validators — and finalized history cannot fork while at most
`2q - n - 1` validators are Byzantine. Between those bounds the group halts
rather than forks. Sizes `n = 3f+1` (4, 7, 10, …) have `q = 2f+1` and equal
liveness and safety budgets `f`; the intermediate sizes `3f+2` and `3f+3`
keep the same liveness budget and add fork-safety margin.

For `1 <= n < 4` the formula gives `f = 0` and `q = n` (unanimous small-set
mode): every validator must participate in every certificate, so the group
has no claimed Byzantine fault tolerance and no tolerance for an unavailable
validator. It is permitted for development and small tests; CLI and Bracken
display a persistent warning while participating in such a group. Validator
URLs and operator labels are committed state; operator labels are
informational and do not cryptographically prove independence.

Height zero is the genesis checkpoint:

```text
block_hash   = genesis event id
state_root   = hash(canonical initial application state)
history_root = hash(["fern-bft-history", genesis event id])
epoch        = 0
```

## 4. Validator ingress

An active validator integrity-checks and state-checks an event before placing
it in its persistent mempool. It records the first local acceptance time once
and returns a signed `IngressReceipt`:

```text
["ingress_receipt", group, chain_id, epoch, event_id,
 validator, first_seen_ms]
```

A client sends to at least `f+1` validators and preferably all validators.
One receipt is enough to display an event as pending. `f+1` receipts prove that
at least one honest validator has accepted it under the configured fault
assumption, but they do not finalize the event.

Validators gossip accepted user-signed events. A receipt cannot make an
invalid event valid and is not a consensus vote.

## 5. Candidates and timestamp observations

For each `(epoch,height,round)`, the deterministic proposer selects a bounded
ordered list of ordinary pending events and at most one governance event.
Ordinary events execute first against the incoming state; governance executes
last. The proposer signs a `Candidate` bound to:

Admission of governance ends the ordinary batching wait and starts consensus
for the next available height. It does not alter an already signed proposal or
make pending governance authoritative before a commit.

```text
["candidate", group, chain_id, epoch, height, round,
 previous_block_hash, previous_state_root, ordinary_event_ids,
 governance_event_id_or_null, proposer]
```

Candidate messages carry the complete events. A validator signs at most one
timestamp observation for a round, and only after it possesses and validates
the complete candidate. Its vector contains its stable local first-seen time
for every candidate event, including governance when present:

```text
["timestamp_observation", group, chain_id, epoch, height, round,
 candidate_id, validator, observed_ms]
```

The proposer needs exactly `q` valid observation vectors. The certified time for
each event is the integer median at the corresponding vector position. The
observation bundle also proves that a quorum possessed every proposed event.
Certified time is approximate evidence, never canonical order.

## 6. Blocks and proposals

A block commits to:

- protocol, group, chain ID, epoch and height;
- the previous block hash and incoming state root;
- the signed candidate and a sorted `q`-member observation bundle;
- every derived certified timestamp;
- the post-execution application state root;
- the cumulative history root.

The block ID is the SHA-256 of its canonical representation. The history root
is the SHA-256 of a domain-separated payload containing the previous history
root and the exact ordered event IDs.

A Tendermint proposal binds a block to the current consensus round and is
signed by that round's deterministic proposer. A reproposal may carry a
`valid_round` and the corresponding quorum of prevotes for the identical block.
Validators reject missing, malformed or unverifiable proof.

## 7. Votes, locks and commit

Vote phases are `prevote` and `precommit`. A vote signs:

```text
["vote", group, chain_id, epoch, height, round, phase,
 block_id_or_null, validator]
```

A validator writes its own vote durably before transmitting it and never signs
two values for the same `(group,epoch,height,round,phase)`, including across a
restart.

For a valid proposal a validator prevotes its block when:

- it is unlocked;
- it is already locked on that block; or
- the proposal contains a valid prevote quorum from a round not older than its
  current lock.

Otherwise it prevotes nil. On `q` prevotes for a block, it durably locks
that block and round before precommitting it. On `q` nil prevotes it may
precommit nil. A block is final only with `q` valid matching precommits from
the block's active epoch. Finalized history never reorganizes.

Timeouts can only cause nil votes or a higher round. They never authorize a
block, clear a lock without proof, reduce a quorum, or substitute local state
for missing evidence.

An implementation must keep waiting through unrelated traffic until the
current phase's proposal/quorum condition is satisfied or the phase timeout
expires. Receiving one valid but insufficient message is not itself a phase
transition.

Seeing signed messages for a future round from at least `f+1` distinct active
validators moves a validator to that round. This is also the rule in unanimous
small-set mode: those sets have derived `f=0`, so one authenticated
future-round sender is sufficient. Without this rule, validators restarting at
round 0 can remain permanently phase-offset from a survivor that advanced while
quorum was unavailable. A malicious validator can exploit the zero-fault mode
to disrupt liveness, but cannot lower the unanimous commit quorum or override
an honest validator's durable lock.

## 8. Deterministic execution

Every validator and client verifies the complete chain from a trusted genesis
or verified checkpoint. For every block it checks signatures, sizes,
sequences, schemas, membership, bans, admin authority, observation medians,
state execution, roots and commit quorum.

Block position `(height, position)` is the only canonical event order. Events
are checked and applied sequentially. A governance event is checked against
the state after the ordinary batch and applied last.

Privileged governance types are:

```text
invite, kick, ban, unban, admin_add, admin_remove,
validator_update, metadata_update,
chat.channel_create, chat.channel_update, chat.channel_delete,
chat.settings_update
```

At most one of these appears in a block. Join, leave, nickname, message and
reaction events may be batched normally. An invalid event invalidates the
whole proposed block; validators do not partially execute proposals.

Ban expiration is evaluated using an event's certified timestamp. A message
finalized after a permanent ban is unauthorized regardless of its author `ts`
or any causal metadata.

## 9. Validator epochs

`validator_update` replaces the complete validator set and increments the
epoch after the transition block commits. The old epoch alone votes on that
block; the new epoch starts at the next height.

Every newly added validator must provide a signed `SyncReady` for the same
pre-transition checkpoint:

```text
["sync_ready", group, chain_id, from_epoch, to_epoch, validator,
 checkpoint_height, checkpoint_block_hash, history_root, byte_count]
```

The readiness objects are included in the governance event. They must cover
every newly added validator and the block immediately preceding the transition
proposal. A new validator never votes on the event granting it authority.

FERN-BFT has no automatic recovery when the old epoch has lost quorum. The
group freezes rather than accepting a unilateral fork.

## 10. Synchronization and admission

Validators and clients synchronize committed blocks, not loose historical
events. A signed history manifest identifies the checkpoint, roots, counts and
logical bytes before bodies are transferred. Block hashes and the commit chain
make transfer content-addressed and independently verifiable.

`logical_bytes` is deterministic: it is the UTF-8 canonical byte length of the
genesis object plus every complete commit object through the checkpoint. A
validator signs readiness only after its recomputed value matches exactly.

Hosting is a local resource decision. A non-genesis history is automatically
admitted only under configured policy, normally after two locally trusted,
independent current validators sign matching hosting attestations for the same
manifest. This web of trust does not affect consensus truth. Genesis hosting
requires an explicit offer, configured open-development policy or operator
approval.

A prospective validator may prepare locally through operator action or
remotely through the `request_readiness` action, which names the group and
candidate source endpoints. Remote preparation applies the same local
admission policy: the validator refuses unless the configured number of
locally trusted, independently labelled active validators sign matching
hosting attestations for one manifest and the history fits the local byte
budget. It is never a `manual` override. On acceptance the validator
synchronizes and verifies the fixed history, persists it, and returns the
signed `SyncReady`. Local and remote preparation are equivalent: both produce
the same exact-checkpoint readiness and record the same admission evidence.

An active validator retains the complete history. It may refuse activation
before signing `SyncReady`; it may not silently prune committed history after
joining an epoch.

An active validator that falls behind requires signed ahead statuses from at
least the current set's round-catch-up threshold (`f+1`, or one in unanimous
small-set mode) before pausing consensus. It then verifies every missing commit
from its existing trusted checkpoint and resumes only from the resulting
durable state. This mechanism cannot recover an epoch that has lost quorum and
never treats statuses themselves as commits.

## 11. Client-visible states

Clients expose only:

- `pending`: an author-signed event accepted through live validator ingress;
- `finalized`: an event in a verified committed block, with certified time and
  permanent `(height,position)`.

Pending governance is never rendered as authoritative. A pending event can be
rejected, superseded by another event using the same author sequence, or remain
pending indefinitely.

Validators also sign their current status, including chain ID, height, block
hash, state/history roots, epoch, validator set and logical byte count. Clients
reject a status whose signer is not in its claimed active set and report any
reachable checkpoint that conflicts with the locally verified chain.

## 12. Safety boundary

In standard mode, while at most `2q - n - 1` validators in an epoch are
Byzantine, two conflicting blocks cannot both obtain valid commits, and while
at most `n - q` are unavailable or Byzantine the group keeps committing.
Between those bounds a group halts rather than forks. Unanimous small sets
(`n < 4`, `q = n`) halt whenever any validator is unavailable and carry no
Byzantine fault-tolerance claim. More Byzantine validators than the safety
bound, obsolete validator-key compromise, a bad freshness anchor, or a faulty
implementation can violate assumptions. A quorum outage halts progress.
User signatures still prevent validators from forging user or admin actions
even outside the normal fault assumption.
