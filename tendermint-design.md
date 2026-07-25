# FERN Per-Group Tendermint Design

## Status

This document is the design input for the protocol implemented on the
`FERN-BFT` branch. The body preserves the future-tense reasoning used before
implementation; it is not the current wire-format specification and should not
be read as an exact description of every implementation choice.

See [architecture.md](architecture.md) for the implemented architecture,
[bft-spec.md](bft-spec.md) for concrete encodings and validation rules, and
[BFT-NOTES.md](BFT-NOTES.md) for deviations and known limitations. The legacy
DAG protocol remains in [spec.md](spec.md) as historical material.

One post-design accommodation is especially important: the implementation
allows one to three validators as a unanimous `f=0` development/test mode,
with quorum equal to the complete set. The original `3f+1` analysis below still
defines standard BFT operation. See the current specification for the exact
rule and client warning requirements.

## 1. Decision in One Paragraph

Every FERN group will be its own small Tendermint-style Byzantine replicated
log, operated by a validator set selected by that group. Users continue to
author and sign messages and governance actions; validators verify them,
disseminate them, attach first-seen time evidence, and agree on one irreversible
order. Ordinary messages appear immediately through gossip, then a roughly
twenty-second block batches many messages, their individual quorum-derived
timestamps and their permanent order into one commit.
Governance is ordered by the same log and cannot be evaded by branching from
old state. Validator replacement uses explicit epochs and a sync-before-
activation handoff. A separate, subjective validator web of trust controls hosting:
by default a validator automatically accepts a history when two independently
trusted current validators attest the same checkpoint. The WoT does not define
group truth or a global validator set.

## 2. Why FERN Is Changing Direction

The current DAG proves event integrity and causality, but it cannot prove
freshness. An author can create a new event after a ban while referencing an
old pre-ban parent. A deterministic topological sort must place parents before
children, but it cannot prove whether that concurrent branch was really made
before or after the ban.

None of the obvious tie-breakers fixes this safely:

- Author timestamps can be backdated, future-dated or used as a group-freezing
  timestamp ratchet.
- Validator or client arrival order differs between observers.
- Event-ID tie-breaking can be ground by an attacker.
- DAG depth proves distance from genesis, not recency or authority.
- Matrix-style state resolution can converge, but it necessarily gives
  conservative or gameable answers to genuinely concurrent governance.
- Making revocation win across every concurrent branch prevents ban evasion,
  but can reject real messages sent before a ban and observed late.
- Validator-signed timestamps and finality watermarks can solve the problem, but
  the necessary committees, quorums, intersections and transitions are already
  a Byzantine consensus protocol in indirect form.

FERN needs one closed prefix of group state. Making consensus explicit is
cleaner than hiding it inside increasingly complex DAG resolution rules.

## 3. Properties the Redesign Must Preserve

- User identities and signatures are portable and are not server accounts.
- A group has a durable cryptographic identity independent of any validator.
- No single validator can forge a user's message or an administrator's action.
- No single validator controls ordering, timestamps, storage admission or
  group availability.
- Groups choose and replace their own validators; there is no global authority
  that grants permission for a group to exist.
- Every active validator retains the complete group history from genesis.
- A completely new client or validator can download and verify that history.
- Members connect only to validators, preserving IP separation between users.
- The protocol remains usable for general public-group applications, not only
  one chat interface.
- Ordinary messages appear immediately, receive strong confirmation promptly,
  and have an independently verifiable approximate publication time.
- Within the configured Byzantine fault bound, validators may delay a valid
  message slightly but cannot censor it permanently.

The principal compromise is that a group now depends on its validator quorum
for liveness. FERN remains decentralized and group-sovereign, but is not
trustless or available through an arbitrary network partition.

## 4. Per-Group Consensus Domains

Each group is an independent consensus domain with its own:

- group identifier;
- validator set and fault parameter;
- chain ID and protocol version;
- block height, round and Tendermint locking state;
- deterministic application state;
- message and history commitments;
- validator-set epoch;
- ingress and resource policy.

This is blockchain-shaped because finalized blocks hash their predecessors,
but it is not a cryptocurrency: there is no mining, token, global ledger or
permissionless validator election. It is a replicated state machine for one
group.

Consensus traffic for many groups may share validator connections and processes,
but signatures and votes must be domain-separated by group, chain, epoch,
height, round and vote type. A vote in one group can never be replayed in
another.

The protocol should use a well-studied Tendermint implementation or consensus
core rather than inventing a novel BFT algorithm. A direct one-process-per-
group deployment is useful for an early prototype but will be too expensive
for validators hosting many groups. Production therefore needs a safe way to run
many isolated consensus instances in one validator process or orchestrate many lightweight
instances. That engineering choice remains open; the per-group safety model
must not be weakened merely to simplify multiplexing.

## 5. Fault Model and Quorums

For a group configured to tolerate `f` Byzantine validators:

```text
n = 3f + 1 validators
q = 2f + 1 votes for a quorum
```

Examples:

| Byzantine faults tolerated | Validators | Quorum |
|---:|---:|---:|
| 1 | 4 | 3 |
| 2 | 7 | 5 |
| 3 | 10 | 7 |

Under eventual network synchrony and while no more than `f` validators are
Byzantine, Tendermint provides safety and liveness. `f+1` Byzantine validators
are outside the security assumption: they can halt the group and may be able
to violate safety by equivocating and splitting honest votes.

Four validators are therefore the minimum meaningful production configuration
that tolerates one faulty validator. More keys do not help if they are operated
by the same person, company, hosting account or legal jurisdiction. Clients and
validator operators should display operator diversity, not only validator count.

### Why `f+1` and `2f+1` both appear

They prove different things:

- Reaching `f+1` validators guarantees that at least one is honest. This is
  enough to ensure that a valid message has reached an honest dissemination
  path.
- A `2f+1` quorum intersects every other quorum in at least `f+1` validators.
  This supports timestamp observation bundles and Tendermint commits.

Reaching `f+1` is therefore useful as early propagation assurance, but the
event remains pending. Its authoritative timestamp, order and state effect come
only from the committed block.

### Tendermint round mechanics

For each block height, a deterministic proposer rotation selects one validator
for round zero. The proposer fixes a candidate list of available pending
messages and, if present, one valid governance action, collects the block-level
timestamp observation quorum described below, and builds the complete proposal.

Validators then follow the normal Tendermint phases:

1. **Propose:** receive the proposed block and all data needed to execute it.
2. **Prevote:** vote for its hash only if the complete data is available, every
   event and certificate is valid, required eligible messages are not being
   censored, and deterministic execution produces the claimed state root.
   Otherwise prevote nil.
3. **Precommit:** after observing `2f+1` matching prevotes, lock and precommit
   that block according to Tendermint's locking rules.
4. **Commit:** `2f+1` matching precommits finalize the block. There are no
   probabilistic confirmations or later reorganizations.

If a phase times out, the height enters a higher round with another proposer.
Locks and valid-block evidence prevent honest validators from finalizing a
conflicting proposal across rounds. Every proposal and vote is signed and
retained as audit evidence; equivocation can therefore be proven and used for
group removal and validator reputation even though FERN has no stake to slash.

FERN should not modify these safety-critical locking rules. Its custom logic is
the deterministic application validity, message availability, inclusion and
validator-transition policy around the consensus core.

## 6. Validators Execute the Protocol State Machine

Validators do not blindly order every integrity-valid object and leave all
meaning to clients. Before voting for an event or block, each validator
deterministically checks:

- canonical encoding, hash and author signature;
- group, protocol version, epoch and size limits;
- membership and posting authorization;
- admin or owner authority for governance events;
- ban, removal and capability state;
- sequence, replay and resource-limit rules;
- deterministic core event schemas;
- block execution and resulting state roots.

This is necessary because consensus over unauthorized bans or messages would
make the canonical log internally contradictory. Clients independently repeat
the same checks rather than trusting a validator RPC response.

Application events may remain opaque if their application-specific meaning
does not affect core group state. Validators still enforce the common envelope,
membership, namespace policy and resource limits. An application rule that
does affect consensus must be a versioned deterministic module committed by
group state; arbitrary server-local application logic cannot affect validity.

Per-IP rate limiting is intentionally not part of deterministic execution.
Different validators observe different connections, NATs and addresses. It is
a local ingress decision. Deterministic block limits place an absolute bound on
canonical history growth; per-validator sponsorship quotas are deferred
hardening described below.

## 7. The Two Stages of an Ordinary Message

FERN separates immediate visibility from canonical block finality.

```text
user-signed event
    -> validator ingress and gossip
    -> pending visibility after reaching an honest validator
    -> block-batched timestamp observations
    -> Tendermint commit: timestamp, order and state are finalized together
```

### 7.1 Pending visibility

The client publishes to at least `f+1` current validators, preferably more in
parallel. If at most `f` are Byzantine, at least one honest validator receives
the event, validates it, persists its local first-seen time and gossips it to
the committee and subscribers.

Clients display the signed event immediately as **pending**, using a clearly
provisional local or ingress time. Sending to `f+1` establishes an honest
dissemination path; it is not a quorum certificate and does not make the event
authoritative.

### 7.2 Finality with a per-message timestamp

Blocks use a target maximum batching window of roughly twenty seconds. At the
end of the window, or earlier when the block is full or urgent governance
requires it, the proposer fixes an ordered candidate event list. Every
validator has retained its first-seen time for each candidate event.

The validators then produce timestamp observation vectors aligned with the
candidate list:

```text
events:      [A,       B,       C]
validator 1: [03.140,  07.821,  18.002]
validator 2: [03.221,  07.790,  18.051]
validator 3: [03.184,  07.846,  18.030]
```

Each validator signs the whole vector, or a commitment to it, once. The
signature is bound to the group, epoch, height, round and a `candidate_hash`
covering the previous block, incoming state, exact ordered event list and any
governance event. This avoids circularly signing a final block hash that itself
contains the observation signatures. A validator signs only after validating
and storing every candidate event, so the same bundle also establishes quorum
data availability. The proposer selects a valid `2f+1` observation quorum,
calculates the deterministic median for every event, and includes the
observation bundle and derived timestamps in the block proposal. Validators
verify the bundle before voting for the block.

The normal Tendermint commit then finalizes, in one decision:

- all event bodies;
- each event's certified timestamp;
- each event's `(block height, position)` order;
- the resulting application state.

The block contains or commits to the full observation vectors needed for
independent verification. Compact time deltas keep this small: with a quorum of
three and four-byte observations, the raw time evidence is about twelve bytes
per message, while only one observation-vector signature per validator is
needed for the entire block. Merkle commitments may support selective proofs,
but full validators retain the complete vectors.

There is no separate per-message `MessageCertificate`. That design would have
required one timestamp quorum per message followed by another quorum for block
finality, undermining the storage and communication savings of batching. A
block-level observation quorum preserves individual timestamps while
amortizing signatures across every message in the block.

Once the block receives a `2f+1` Tendermint commit, each event is finalized
history. Under normal synchrony, a new client should therefore be able to fetch
all messages older than roughly one block window. Twenty seconds is a target,
not an unconditional deadline: a failed round, network partition, full block
or Byzantine proposer can extend it.

## 8. Blocks and Governance Ordering

A conceptual finalized block package contains:

```text
group and protocol domain
height and previous block hash
validator epoch and current state version
ordered ordinary-message batch or batch roots
2f+1 signed timestamp observation vectors or commitments
derived certified timestamp for each event
zero or one governance event
message/history root
post-state root
timestamp and resource-accounting commitments
next-validator-set data when applicable
associated 2f+1 commit signatures
```

Ordinary messages are ordered before the governance event in the same block,
and a block contains at most one governance event. Messages in that block are
evaluated against the incoming state; the governance event is then applied and
produces the state for the next block.

This gives bans and validator changes an exact boundary. It avoids pretending
that an imprecise wall-clock timestamp determines whether a message preceded a
ban, and it avoids complicated ordering between several concurrent governance
changes. Multiple governance actions queue across consecutive blocks.

Before a governance event closes a state version, the old state admits the
ordinary messages selected earlier in that transition block. The governance
event is applied last. Messages not selected before the boundary remain pending
and are evaluated against the new state in a later block; an earlier first-seen
timestamp never overrides canonical governance order.

The exact candidate-list cutoff and fair-inclusion rule remain important spec
work. They must resolve races without allowing an endless stream of messages to
postpone governance forever or a proposer to exploit the cutoff selectively.

Blocks target a maximum batching window of roughly twenty seconds, but close
early when full and may close early for governance. The cadence is an
operational finalization target, not an ordering rule. Empty blocks may be
suppressed if checkpoint, liveness and timeout semantics remain unambiguous.

## 9. Censorship Resistance and Availability

Tendermint by itself prevents forks; it does not automatically make proposer
selection fair. FERN therefore requires authenticated mempool gossip, bounded
queues, deterministic block limits and a fair proposal policy:

1. An honest ingress validator validates, persists and gossips a valid event.
2. Other honest validators retain the event and their stable first-seen time.
3. Proposers select valid pending events using the specified queue and block
   limits rather than an unconstrained private ordering policy.
4. Proposer rotation lets a later honest proposer include messages delayed by
   a Byzantine proposer.

Therefore, if a client reaches `f+1` validators, the network becomes eventually
synchronous, the event is authorized and the client is within ingress limits,
at least one honest validator can disseminate it and an honest proposer can
eventually place it in a block that the honest quorum finalizes without
Byzantine cooperation.

"Validators cannot censor" is always conditional on that fault and network
model. A quorum outage can freeze the group, and more than `f` colluding
validators exceed the guarantee. Even a malicious quorum cannot forge a
message from an honest user because clients continue to verify user signatures.

## 10. Certified Message Time

Author-provided time may remain as UI metadata, but it has no authority over
ordering, bans, expiration or consensus.

Each validator persistently records the time at which it first accepted and
stored the complete event. At block construction, one signature covers that
validator's observation vector for every candidate event. The block carries a
deterministically selected `2f+1` vector quorum, and the displayed timestamp for
each event is the median of its observations in that quorum.

The timestamp means approximately:

> A quorum of this group's validators had received the complete message around
> this time.

It does not prove when the user clicked Send. Accuracy depends on honest clock
quality and prompt gossip. Faulty validators may lie or withhold, but with at
most `f` Byzantine members the median of a `2f+1` quorum remains within the
range of honest observations selected for that event. A message withheld until
the proposal is truthfully timestamped near the later time at which the quorum
actually receives it. Exact precision, time-delta encoding, clock bounds,
quorum selection and external clock anchoring remain open parameters.

Most importantly, time does not decide canonical order. `(block height, event
position)` does. Two concurrent messages may have certified times in one order
and block positions in another; all clients still agree, while both messages
retain honest approximate timestamps.

Block headers also carry a consensus time for monotonic ledger operation, but
that shared block time is not substituted for the per-message observations.
This is why FERN can keep relatively slow, storage-efficient blocks without
giving every message up to twenty seconds of timestamp error.

## 11. Spam-Controlled Live Ingress

Every validator applies a local per-IP token bucket to direct client
connections. That protects its public ingress endpoint but cannot be replayed
or verified later because an IP address is a receiver-local observation.

The initial BFT design relies on three simpler controls:

- a deterministic maximum block byte count;
- a roughly twenty-second block cadence, with blocks closing early when full;
- authenticated per-peer bandwidth, request and mempool limits between
  validators.

Together, the first two create an objective maximum canonical history-growth
rate. They do not prove that historical validators treated IP addresses fairly,
and rotating IPs or botnets can still consume an honest validator's public
allowance. These are accepted limits of the initial design.

Per-validator sponsorship quotas are deliberately deferred. They should later
let each validator sponsor only a deterministic number of messages or bytes per
consensus period. This would stop one Byzantine validator from bypassing client
IP limits over the validator network and monopolizing all block capacity while
leaving honest validators' shares available.

The sponsorship mechanism is valuable fault isolation, but it is not required
to establish the initial chain-wide storage ceiling. Deferring it keeps the
first Tendermint implementation smaller; the event and block formats should
reserve a clean versioned extension point for it.

## 12. Why QCs Do Not Solve Historical Sync Spam

A quorum certificate proves only that the validator keys recognized by a
history signed something. It does not prove that those validators were
independent, honest, long-lived or worthy of an unrelated validator's storage.

An attacker can create an entirely Sybil validator set, manufacture a large
self-consistent history and produce valid QCs, manifests, timestamps and
validator transitions. Deterministic block limits do not make that history
worthy of storage: an attacker can fabricate a long history or repeat the
attack across many groups.

Consequently a joining validator performs two separate checks:

- **Protocol verification:** Is the history internally valid relative to the
  group's own identity, validator transitions and certificates?
- **Resource admission:** Does this validator have an independent reason to spend
  bandwidth and permanent disk on that history?

Cryptographic QCs answer only the first question.

## 13. Subjective Global Validator Web of Trust

FERN retains a validator web of trust for resource admission. It is global in the
sense that validator reputations and attestations can be reused across groups, but
there is no globally canonical trust graph, trust score or approved validator list.
Every receiving validator chooses its own direct witnesses, delegation rules,
thresholds and storage policy.

The WoT is directional and subjective:

> I am willing to inherit some storage risk from the ingress policies of these
> operators.

It does not mean:

> These operators decide which histories are true for every FERN group.

### Default hosting threshold

A validator automatically accepts a hosting request when at least two independently
trusted validators in the group's current active validator set provide fresh,
matching hosting attestations. The default local policy is therefore:

```text
min_trusted_current_hosts = 2
```

Operators may configure one witness for small or private deployments, or a
higher threshold for more conservative infrastructure. The threshold counts
independent operator trust domains, not merely distinct validator keys.
Two is the default because it prevents one compromised or misconfigured trusted
validator from laundering an arbitrary history, without requiring the prospective
host to trust the entire current committee.

Merely naming a trusted validator in group state is insufficient. Each witness must
be an actually activated current validator and sign an attestation bound to:

- the group and current validator epoch;
- the exact checkpoint and history root;
- the total history byte count;
- a statement that it currently holds the complete history;
- an expiry time.

The prospective host accepts only matching attestations for the same history.
It still fetches the manifest before any bodies and applies hard local staging,
bandwidth and permanent-storage limits.

### Transitive admission

The prospective host does not need directly to trust every historical
validator set. By trusting two current hosts, it delegates the historical
resource-admission judgement to operators that already downloaded, verified
and retained the complete history. Those validators in turn admitted the group
through their trusted predecessors, allowing hosting trust to move forward as
the group changes validators.

To preserve this recursive guarantee, a validator's activation evidence should
record whether it joined under the normal WoT threshold or through a weaker
manual/one-witness override, together with its predecessor witnesses and
accepted checkpoint. A validator requiring two-witness provenance must not silently
treat a weaker override as equivalent.

Admission dependencies point to validators that were already active in the
previous epoch. Newly added validators cannot cite each other to manufacture a
circular trust chain. Genesis is the base case and requires direct operator
approval, directly trusted initial hosts, or another explicitly configured
admission mechanism.

Two trusted current hosts are sufficient for the default **storage admission**
decision. They do not prove that the complete current committee satisfies its
BFT fault bound. A validator separately decides whether the proposed validator set
has enough operator independence and acceptable Byzantine risk for it to join
that committee.

The existing two-tier heal idea becomes a two-tier history-admission policy:

- **Trusted fast sync:** high-throughput transfer is allowed when sufficient
  matching attestations are supplied by trusted current validators.
- **Untrusted slow path:** unknown histories receive only small, rate-limited
  staging quotas, header/manifest inspection, proof-of-work or payment if later
  introduced, or explicit operator approval.

No group can force a validator to host it by listing that validator in its set.
Hosting always requires a signed offer or acceptance from the validator.

Alternative Sybil costs could replace some WoT reliance: storage payments,
bonds, proof of work, limited hosting vouchers or manual approval. They are not
the current default because they add economics, poor mobile UX or a different
authority. Without one of those scarce resources, a subjective WoT is essential
for automatic free hosting of arbitrary permissionless groups.

## 14. Safe Full-History Synchronization

A prospective validator first fetches a small, quorum-signed
`HistoryManifest`, not message bodies. At minimum it commits to:

- group, protocol version, height and validator epoch;
- latest block and checkpoint hashes;
- history and state roots;
- event, block and logical byte counts;
- historical validator and policy commitments;
- current maximum growth policy.

The validator obtains matching attestations from the configured minimum number of
locally trusted current validators, checks their activation and admission
evidence, checks its aggregate temporary and permanent storage budgets, and
explicitly accepts or rejects the hosting request. Repeated requests and
manifest work are themselves rate-limited.

If accepted, it synchronizes to one fixed checkpoint using content-addressed
chunks and inclusion proofs. It rejects unreferenced, duplicate or oversized
objects and never exposes an anonymous "upload possible old events" API. It
then verifies from genesis:

1. the group identity and genesis;
2. every author signature and event hash;
3. the chain of historical validator sets;
4. every Tendermint commit and state transition;
5. every timestamp observation bundle and per-event median calculation;
6. block, history and application-state roots;
7. the complete availability of every committed event body.

After catching the bounded tail, the new validator signs
`SyncReady(checkpoint, history_root, byte_count)`. It cannot vote before the
old committee finalizes its activation.

A manifest prevents hidden size, proofs prevent arbitrary injection, local
quotas bound work, and the trusted-current-host threshold decides whether the
internally valid history deserves resources. All four are necessary; none
substitutes for the others.

Full history grows without bound over a group's lifetime. FERN cannot promise
infinite storage. A validator must be able to decline before activation, and a
group must select validators willing to meet its current size and advertised
future growth. Snapshots may accelerate verification later but do not replace
the complete archive requirement.

## 15. Validator-Set Epochs and Handoff

Here, an **epoch** is simply the interval during which one exact validator set
is authoritative. It is not a timestamp bucket and does not independently
order messages.

The group owner or other currently authorized controller may propose changing
the validator set at any time. The normal transition is:

1. A signed governance proposal identifies the complete next set, its keys,
   endpoints, fault parameter and policy.
2. Every newly added validator explicitly accepts the hosting request under
   its local policy—normally using two trusted active validators from the old
   epoch—and signs admission evidence identifying the accepted checkpoint and
   witness class.
3. New validators download and verify the complete history to a fixed
   checkpoint while the old epoch continues operating.
4. Each new validator signs a readiness statement for the same history root.
5. The old epoch enters a bounded closing/draining phase. It fixes the final
   old-state candidate list, certifies that block's per-message timestamps and
   commits the selected in-flight messages. Messages outside the cutoff remain
   pending for the new epoch.
6. The final old-epoch transition block applies the validator change last and
   commits the old history root, new set, readiness evidence and exact
   activation height.
7. The new set begins the next height and epoch. It never votes on the event
   that granted it authority.

This permits replacing one validator or the whole set without losing history.
Old public keys and epoch definitions remain in the permanent log so new
clients can verify old commits.

The old committee needs a quorum to authorize the transition. If it has already
lost quorum, allowing the owner to replace it unilaterally would also allow a
partitioned or compromised owner to fork finalized history. FERN must still
choose an explicit catastrophic-recovery policy: permanent freeze, delayed
owner/admin recovery with weakened finality, or a clearly identified successor
group/social fork. There is no mechanism that guarantees both unconditional
finality and unilateral recovery from every quorum failure.

## 16. Group Addresses, Freshness and Long-Range Attacks

A shareable group reference should contain or resolve to:

- the permanent group public key;
- network and protocol identifiers;
- current validator keys and endpoints;
- the current epoch;
- a recent finalized checkpoint height and hash;
- freshness or expiry metadata.

The group key and full transition chain establish identity. The recent
checkpoint is a weak-subjectivity/freshness anchor: it protects a new client
from being shown an alternative history signed using compromised obsolete
validator keys and avoids relying on validators that have long since gone
offline.

An old link is not automatically proof of an attack, but it provides weaker
freshness and may point only to dead or superseded validators. Clients should
obtain a recent group reference through the same social or authenticated
channel by which they learn which group they intended to join, then verify the
entire available certificate chain rather than blindly trusting one RPC server.

## 17. Client Model and Independent Verification

Clients query multiple current validators for availability and split detection,
but accept cryptographic proofs rather than majority-voting over unproven RPC
answers. From a complete history and a recent trusted group reference, every
correct client can derive the same:

- validator epochs and commit validity;
- certified message timestamps;
- canonical `(height, position)` event order;
- membership, moderation and application state;
- current validator set and checkpoint;
- evidence of validator equivocation or invalid proposals.

Clients retain two authoritative-status states:

- **Pending:** user-signed and received through a live gossip path, with a
  provisional display time.
- **Finalized:** included in a Tendermint-committed block, with a certified
  per-message timestamp and permanent position.

The UI must not present pending governance as authoritative.

## 18. Why Per-Group BFT, Not Global FBA

A global federated Byzantine agreement network was considered because the validator
WoT already creates relationships between operators. It was rejected because
the scope of those relationships is different.

In the selected design, a WoT decision means only that one validator will or will
not host a history. Validators may disagree without creating conflicting group
state. A community can form a completely separate trust ecosystem and still
run sovereign groups.

In global FBA, trust choices determine the quorum topology that orders every
group. Quorum intersection pressures independent communities toward a common
trusted core; compromise, censorship, outage or misconfiguration in that core
has network-wide impact. Existing validators admitting new validators would
also create a permissioned incumbent consortium. Hosting all groups everywhere
would amplify storage spam, while sharding them among validator subsets would
reintroduce something close to per-group committees.

Per-group BFT plus a single mandatory canonical WoT could become global
authority in disguise. FERN must therefore keep WoT policy plural and local.
Group validators need to accept their group's overall `f`-fault assumption,
but they do not all need mutually to approve one another through a global WoT.

## 19. Relationship to the Existing DAG

The event DAG will no longer determine authorization, governance order or
finality. The consensus log closes the stale-parent and branch-from-genesis
attacks directly: after a ban is committed, a later block evaluates the banned
author against post-ban state regardless of any parent reference.

Events may retain optional causal, reply or application dependency references.
Those are useful metadata and may assist dissemination, but cannot move an
event to an earlier state. Gap detection and healing primarily become ordered
block/batch synchronization rather than discovery of an authoritative DAG
branch.

The current DAG protocol cannot be upgraded silently. FERN will need an
explicit protocol version and a migration boundary or newly created BFT group
identity. Legacy unsigned timing evidence cannot be retroactively converted
into BFT finality.

## 20. Future Encryption Compatibility

BFT consensus is compatible with future encrypted direct messages or encrypted
groups. Validators can order ciphertext and validate signatures, membership,
envelope sizes and key-epoch metadata without seeing plaintext. The hard part
remains key distribution, membership-driven rekeying, metadata leakage and
moderation of content validators cannot read; DAG versus BFT does not remove
those problems. Encryption is not part of the current redesign.

## 21. Decisions Still Open

The architecture is selected, but the following require specification or
prototyping:

1. The maintained Tendermint/CometBFT core or library and safe multi-group
   execution model.
2. Exact parameters around the roughly twenty-second maximum block window,
   byte limits, early closing, checkpoint frequency and empty-block rule.
3. Timestamp observation-vector encoding, candidate binding, quorum selection,
   Merkle proof format and retained evidence.
4. The exact candidate cutoff, fair-inclusion and governance drain/close race
   protocol.
5. Certified-time precision, clock bounds and deterministic median rules.
6. The future sponsorship-quota format and the protocol version that activates
   it; sponsorship is not required in the initial BFT release.
7. Local WoT representation, distinct-operator counting, hosting-attestation
   format, override provenance and genesis bootstrap.
8. The owner/admin authorization model for validator changes and whether
   threshold group governance is required for high-risk operations.
9. Whole-set reconfiguration details and readiness proof format.
10. Catastrophic recovery after quorum loss.
11. Recent-checkpoint distribution, expiry and long-range conflict handling.
12. Migration from existing DAG groups and compatibility with legacy clients.
13. Retention enforcement, capacity leases and what happens when an active
    validator can no longer store the complete history.

## 22. Final Security Boundary

The design intentionally assigns different jobs to different evidence:

```text
User signature
    proves who authored an immutable event

Local IP policy and deterministic block limits
    control public ingress and bound total canonical history growth

Block timestamp observation bundle (2f+1)
    proves each finalized message's approximate quorum first-seen time

Tendermint commit (2f+1)
    finalizes the block's messages, timestamps, order and group state

Historical commit chain
    proves internal consistency from the group trust anchor

Two receiver-trusted current-host attestations by default
    decide whether that internally valid history deserves hosting resources

Recent group checkpoint
    anchors freshness against obsolete-key and long-range histories
```

No one of these objects solves all problems. In particular, consensus
certificates are not reputation, per-message timestamps are not order, and a
storage WoT is not global consensus. Keeping those boundaries explicit is the
central design decision of the Tendermint revision.
