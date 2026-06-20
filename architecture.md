# FERN — Fault-tolerant Event Relay Network
## Architecture Overview

---

## 1. Overview

FERN is a decentralised, censorship-resistant protocol for public group chats. Identity and groups are cryptographic keypairs, not server accounts. All content is expressed as signed, immutable events arranged in a hash-linked Directed Acyclic Graph (DAG). Relays are interchangeable infrastructure with no authority over group state, membership, or identity.

FERN is designed as a **general-purpose protocol** for applications that are fundamentally "public group chat" at their core. The protocol itself defines transport, identity, group management, and completeness verification. Individual applications (chat clients, polling tools, collaborative boards, etc.) define their own event types and content schemas on top of the protocol.

---

## 2. Design Goals

- **User identity is portable** — not tied to any server or relay
- **Group identity is not owned** by any relay; groups are cryptographic entities
- **No relay can forge, rewrite, or silently censor messages**
- **Groups survive relay failures and migrations**
- **History is self-healing** through normal client behaviour
- **Suitable for large public group chats** (Discord-server-like)
- **IP hiding between members** — clients never connect to each other directly
- **General-purpose foundation** — apps define their own event types on top

## 2.1 Non-Goals

- **End-to-end encryption** — designed for public groups
- **Cryptographic impossibility of censorship** — without consensus this is unattainable; the target is *detection and self-healing*
- **Guaranteed message delivery** — best-effort with strong evidence trails
- **Serverless or peer-to-peer transport** — clients only talk to relays
- **Perfect split-view attack prevention** — defeated probabilistically by vantage diversity, not cryptographically (same trust model as Certificate Transparency)
- **Multi-key threshold governance** — deferred; founder single-key for now, threshold signing is a future extension

---

## 3. Core Insight

Split the responsibilities a traditional chat server holds into independent roles:

| Responsibility | Held by |
|---|---|
| Storing and transporting messages | **Relays** — interchangeable servers anyone can run |
| Making admin decisions (bans, etc.) | **Founder + mods** — their decisions are public, signed events |
| Catching misbehavior | **Every client does this for free** just by reading the chat |

And the resolution of the moderation-vs-censorship paradox:

> **Admin actions (like bans) never delete anything from the log. They are state events that clients fold into a current state machine, then choose what to render. Moderation happens at the display layer, not the storage layer.**

The log is permanently faithful and complete. Each client decides what to actually show. A "show banned messages" mode is trivial — the data is always there. The protocol guarantees the data; the client decides the rendering.

---

## 4. Cryptographic Primitives

- **Keypairs**: Ed25519 throughout. Private keys are 32 bytes, public keys are 32 bytes, signatures are 64 bytes. All encoded as lowercase hex.
- **Hashing**: SHA-256, encoded as lowercase hex.
- **Event ID**: The SHA-256 hash of the event's canonical serialisation (see Section 6.3).

---

## 5. Identity

### 5.1 User Identity

A user identity is an Ed25519 keypair generated locally on the user's device. There is no registration process. A user exists as soon as they generate a keypair. Identity is not tied to any relay or server.

### 5.2 Group Identity

A group has its own Ed25519 keypair, separate from the founder's user keypair. The group's public key **is** the group's identifier — there is no separate `group_id` hash. The group private key is the root of trust for the group's genesis event.

- Used **only** to sign the genesis event
- Should be treated as a high-value secret, stored offline, used once
- The group pubkey is the permanent, shareable group identifier
- A group address is shared as: `<group_pubkey>@<relay1>,<relay2>,<relay3>`

This cleanly separates group identity from founder identity, enabling future key rotation or transfer of ownership.

### 5.3 Relay Identity

Each relay has its own Ed25519 keypair, used for signing receipts and attestations. Clients learn a relay's public key via a relay metadata endpoint (over TLS) on first connection and store it for subsequent verification. The relay's pubkey is its identity for all completeness-layer operations.

### 5.4 Actor Roles

- **Founder** — creates the group, holds the group private key (used once for genesis), and their user key for ongoing mod actions. The founder is the initial mod; subsequent mods can promote/demote other mods (the founder has no special authority beyond being the initial mod).
- **Mods** — pubkeys the founder has designated. Can sign admin actions (invite, kick, ban, unban, promote, relay updates, metadata updates). Flat list, no capability chains.
- **Members** — users who have an active `join` event (and no subsequent `leave` or `kick`). Can post events.
- **Relays** — servers that store and serve events. Multiple per group; clients connect to several.
- **Clients** — what users run. Connect to multiple relays, post, verify, monitor. Every client is automatically also a monitor — there is no separate monitor role.

No peer-to-peer connections between clients. Clients only talk to relays. IPs are seen by relays, never by other group members. (Clients may use Tor/VPN at their option; the protocol does not mandate it.)

---

## 6. Events

Everything in FERN is an event. Messages, group creation, invites, joins, membership changes, relay list updates — all are expressed as events in the same format.

### 6.1 Event Structure

```json
{
  "id":       "<sha256 of canonical serialisation>",
  "type":     "<event type string>",
  "group":    "<group pubkey hex>",
  "author":   "<author pubkey hex>",
  "parents":  ["<event id hex>", ...],
  "content":  <JSON object, schema defined by type>,
  "ts":       1711234567,
  "tags":     [],
  "sig":      "<ed25519 signature hex>"
}
```

### 6.2 Field Definitions

| Field | Type | Description |
|---|---|---|
| `id` | hex string | SHA-256 of the canonical serialisation |
| `type` | string | Event type identifier (see Section 6.4) |
| `group` | hex string | Public key of the group this event belongs to |
| `author` | hex string | Public key of the event author |
| `parents` | array of hex | IDs of parent events in the DAG (completeness propagation) |
| `content` | JSON object | Type-specific payload, schema defined by `type` |
| `ts` | integer | Unix timestamp in seconds |
| `tags` | array | Reserved for protocol-level extensions. Empty by default. App-specific data goes in `content`, not `tags`. |
| `sig` | hex string | Ed25519 signature over the canonical serialisation |

### 6.3 Canonical Serialisation

The canonical serialisation used for hashing and signing is a JSON array with fields in a fixed order, with no whitespace:

```
[type, group, author, parents, content, ts, tags]
```

- `parents` is sorted lexicographically before serialising
- `content` is serialised as a JSON object (always an object, never a bare string)
- `tags` is sorted: by first element, then subsequent elements lexicographically
- No trailing whitespace or newlines
- `id = SHA256(canonical_serialisation)`
- `sig = Ed25519Sign(privkey, canonical_serialisation)`

### 6.4 Type Namespacing

Event types follow a namespacing convention:

- **Protocol types** are bare strings without a dot: `genesis`, `join`, `ban`, `relay_update`, etc. These are reserved and defined by the protocol specification.
- **App types** use the `appname.local_type` convention: `chat.message`, `chat.reaction`, `poll.vote`, `schedule.event`, etc. The first segment (before the dot) is the application name. Collisions are resolved socially, like package names in npm.
- **`chat` is the official default app namespace**, maintained as part of FERN itself. It covers basic group chat features (messages, reactions, nicknames). Other namespaces (e.g., `poll`, `schedule`, `whiteboard`) can be built on top of FERN by anyone.

Rule: a type containing a `.` is app-namespaced; a type without a `.` is a protocol-reserved type. Apps MUST NOT use bare (no-dot) type names.

The protocol treats all events uniformly for transport, validation, and completeness — it does not interpret `content`. Client-side apps validate content per their known types and ignore unknown types (forward compatibility — new event types don't break old clients).

### 6.5 Verification

To verify an event, a client must confirm:

1. The `id` matches the SHA-256 of the canonical serialisation
2. The `sig` is a valid Ed25519 signature over the canonical serialisation, verifiable with the `author` pubkey
3. The `author` is authorised to publish this event type at this point in the DAG (see Section 8.4)
4. Exception: `genesis` is verified against the group pubkey (the `group` field)

---

## 7. The Causal DAG

### 7.1 Structure

Events form a Directed Acyclic Graph where edges are parent references. Every event except `genesis` must have at least one parent. The genesis event has an empty `parents` array and is the root of the DAG.

When composing an event, the author references the **connected heads** — the most recent event IDs in the local store that are connected all the way back to the group's genesis event, and that no other connected event extends. Multiple parents are valid and represent concurrent branches being merged. This is similar to Git's commit DAG, with one extra safety rule: an event with a missing parent is stored as pending evidence, but is not a valid parent for future events until the missing chain is healed.

**The DAG is for completeness propagation, not replies.** Reply threading is an app-level concern expressed in event `content` (e.g., `{"reply_to": "<event_id>"}` inside a `chat.message` content). The protocol DAG knows nothing about replies.

### 7.2 Why the DAG Exists

The DAG is not for integrity (signatures already defeat forgery) and not for ordering (display order is timestamp-based). It exists for **completeness propagation**:

- If a client receives an event that references an unknown parent, it knows to fetch that parent. The parent's existence is attested by a signed event from a real author.
- A relay that wants to censor message M must also censor every descendant of M, transitively, forever. The censor's surface area grows over time. Each suppressed descendant is itself a new fault.

This raises the bar from "drop a message, hope nobody notices" to "drop an entire conversation tree forever." In active chats (the Discord-like scenario FERN targets), most messages quickly get descendants, so existence propagates naturally through normal chat activity.

### 7.3 Gaps

If a client has an event whose parent hash is unknown, that parent is considered a gap. The child event is disconnected until every missing parent chain has been healed back to genesis. Gaps are:

- **Visible** — the missing hash is known from the child event's `parents` field
- **Specifically addressable** — a client can request the missing event by its exact hash
- **Not fatal to storage** — disconnected events are stored and used as evidence that a missing parent exists
- **Not normal history** — disconnected events are not applied to group state, rendered as ordinary messages, or selected as parents for new events

Clients must never discard events solely because their parents are absent.

### 7.4 Gap Healing

When a client detects a missing parent hash it should:

1. Request the event by ID from every relay on the current canonical relay list
2. If found, verify and store it
3. If found on some relays but not others, publish it to the relays that were missing it (backfill)
4. Recompute the connected set. The child event enters normal state derivation and rendering only after its complete parent chain is connected to genesis.

### 7.5 Honest Limits of the DAG

The DAG is a **force multiplier**, not a completeness proof:

- **Leaf messages with no descendants** get zero propagation benefit. If the last message in a quiet chat is censored, no descendant will ever reference it.
- **All-relays-collude-and-suppress-the-whole-tree** censorship remains undetectable. The censor must win an ever-growing race forever, but if they do, the client has no signal.
- **The DAG doesn't prove which relay received a message.** That's the job of receipts (Section 9.1).
- **The DAG doesn't catch split-view attacks.** That's the job of signed attestations and vantage diversity (Section 9.2).

The DAG is cheap (multiple parent IDs in an array; small client bookkeeping to track heads). It's included because it provides strong value in active chats at minimal cost.

---

## 8. Group State

Group state is derived entirely by replaying the genesis-connected subset of the DAG in timestamp order. There is no separate state database. Any relay or client with the same connected event history will derive identical state. Disconnected events remain in storage for gap healing and evidence, but do not affect group state.

### 8.1 State Model

```
members:   set of pubkeys invited to the group
joined:    set of pubkeys who have joined (have an active join)
banned:    map of pubkey → {until, reason}
mods:      set of pubkeys with moderator privileges
relays:    [url, url, ...]
metadata:  { name, description }
public:    boolean (from genesis)
```

### 8.2 Derivation Rules

Starting from genesis:

- `members` is initialised with the founder pubkey
- `joined` is initialised with the founder pubkey (the founder is automatically joined)
- `mods` is initialised with the founder pubkey
- `relays` is initialised from the genesis content
- `metadata` is initialised from the genesis content
- `public` is initialised from the genesis content
- `banned` is initialised empty

For each subsequent connected event in timestamp order:

| Event type | State change |
|---|---|
| `invite` | Add `invitee` to `members` |
| `join` | Add `author` to `joined` (if authorised — see 8.5) |
| `leave` | Remove `author` from `joined` |
| `kick` | Remove `target` from `joined` and `mods` (keep in `members`) |
| `ban` | Add `target` to `banned` with `until` and `reason`. Ban persists until explicitly lifted. |
| `unban` | Remove `target` from `banned` |
| `mod_add` | Add `target` to `mods` |
| `mod_remove` | Remove `target` from `mods` |
| `relay_update` | Replace `relays` with new value |
| `metadata_update` | Replace `metadata` fields present in content |

### 8.3 Conflict Resolution

When two events have the same timestamp and affect the same state field, the event with the lexicographically greater `id` wins. This rule is deterministic and produces the same result on all clients regardless of event arrival order.

### 8.4 Authorisation

Before applying a state change event, clients verify:

- The `author` pubkey is in `mods` at the point in the DAG immediately before this event
- Exception: `genesis` is verified against the group pubkey
- Exception: `join` and `leave` are verified against the `author` pubkey directly (users can always join or leave for themselves)

Events failing authorisation are discarded silently by clients. Their IDs remain valid as parent references in the DAG if they are connected to genesis. Relays store them regardless (relays do not enforce authorisation).

### 8.5 Ban Semantics

Bans work as follows:

- A `ban` event adds the target to the `banned` map with an optional `until` timestamp and a `reason` string.
- A banned user is removed from `joined` (they cannot post messages).
- A ban **persists** until explicitly lifted by an `unban` event, or until the `until` timestamp passes (if set).
- A `join` event from a banned user is discarded by clients.
- A `kick` does **not** add to the banned map — the user can re-join. A `ban` prevents re-joining.
- Promoting a banned user to mod does **not** automatically lift the ban. The ban must be explicitly lifted via `unban` first.

This makes concurrent admin actions (one bans, another promotes in the same epoch without seeing the ban) predictable: a ban always persists until explicitly lifted.

### 8.6 Posting Authorisation

Only users in the `joined` set (and not in the `banned` set) may publish app-level events (e.g., `chat.message`). Messages from banned or non-joined users are stored by relays but rejected by clients. Relays do not enforce this — it is the client's responsibility.

Users can view group history and state without joining. Joining is required only to post.

---

## 9. Completeness Layer

The hardest problem in the protocol. Solving it perfectly would require consensus (out of scope). The goal is to make censorship **detectable and provable** when attempted, and **self-healing** when relays lag.

The threat model is explicitly scoped to **1-2 misbehaving relays** in a set of curated, trusted relays. All-relays-collude censorship is acknowledged as undetectable and out of scope.

Five mechanisms, stacked:

### 9.1 Receipts

When a relay accepts an event from a client, it returns a signed receipt:

```json
{
  "event_id": "<event id hex>",
  "group":    "<group pubkey hex>",
  "relay":    "<relay pubkey hex>",
  "ts":       1711234567,
  "sig":      "<ed25519 signature by relay key>"
}
```

This means **a relay cannot deny having received an event it accepted**. The author keeps the receipt locally.

Receipts are **author-local and shared on-demand only**. They are NOT events in the DAG. They are NOT routinely gossiped or propagated. They stay on the author's device until needed.

When the author (or any client) detects that a relay's attestation or responses contradict a receipt the author holds, the author publishes a **fraud proof** (Section 9.5) containing the receipt. That is the only time receipts are shared.

This approach adds zero ongoing traffic overhead, keeps receipts cleanly out of the DAG, and avoids fragile propagation mechanisms. The author is the natural prosecutor — they have the strongest motive (their message was censored) and the evidence (their receipts).

### 9.2 Attestations

Each relay periodically publishes a signed attestation committing to everything it knows for a group:

```json
{
  "group":    "<group pubkey hex>",
  "relay":    "<relay pubkey hex>",
  "set_hash": "<sha256 of sorted, newline-concatenated event IDs>",
  "tips":     ["<event id hex>", ...],
  "count":    1452,
  "prev":     "<previous attestation hash hex, or null>",
  "ts":       1711234567,
  "sig":      "<ed25519 signature by relay key>"
}
```

The attestation is a signed commitment: "Here's the hash of everything I have for this group, as of this time."

- **`set_hash`**: SHA-256 of all known event IDs, sorted lexicographically and joined with newlines. If two relays' `set_hash` values match, they have the same set. If they differ, investigation is needed.
- **`tips`**: The current DAG frontier (events with no children). Quick structural comparison.
- **`count`**: Total number of stored events. Quick sanity check.
- **`prev`**: Hash of the relay's previous attestation, forming a per-relay attestation chain. Proves ordering: "Your attestation at T2 is a successor to your attestation at T1."

Attestation issuance rate is configurable per relay (suggested default: every 5 seconds or every 100 new events, whichever comes first). Relays push attestations to subscribed clients automatically. Clients can also request the latest attestation on demand.

Uses a simple sorted-set hash, not a Merkle tree with exclusion proofs. Monitors hold the full known-set and compare directly. Merkle exclusion proofs (allowing third parties to verify non-inclusion without holding the full set) are deferred.

### 9.3 Monitor Pass (Every Client Is a Monitor)

There is no separate monitor role. **Every client that reads a group is automatically a monitor**, because it:

1. Is already required to connect to multiple relays (default K=3).
2. Receives each relay's attestations as part of normal subscription.
3. Holds the full set of events it has seen.
4. Compares relay attestations against its local known-set and against each other.

The audit pass runs on every attestation push:

**For every client (cross-relay comparison):**

- Compare each relay's attestation (`set_hash`, `tips`, `count`) to the client's local known-set and to other relays' attestations.
- If they match: in sync.
- If they differ: investigate. Request specific events the client has that the relay might be missing, and vice versa.
- For events the client has that a relay's attestation doesn't include:
  - If the event's parents are in the relay's set (parent IDs appear in events the relay has): the relay should have this event. Trigger backfill (Section 9.4).
  - If backfill doesn't resolve the discrepancy (relay refuses to integrate): flag in local trust ledger.

**For the author specifically (receipt-based proof):**

- If the author holds a receipt from relay R for event E, but R's attestation (or response to a `get` request) doesn't include E: **provable censorship**. The receipt is signed by R proving it received E; the attestation/response proves E is absent. Publish a fraud proof (Section 9.5). Record in local trust ledger. De-list R locally.

**Split-view defense:**

Because attestations are signed, two clients comparing the attestations they received from the same relay can detect if the relay served them divergent views. Without signatures, a relay could serve different summaries to different clients and neither could prove it. Signed attestations make this provable when caught.

### 9.4 Backfill — The Network Self-Heals

When a client notices a relay is missing an event (detected via attestation divergence or via a gap in the DAG), it doesn't just flag the fault — it fetches the missing event from a sibling relay that has it and republishes it to the lagging relay. The lagging relay either:

- Integrates it (its next attestation converges), or
- Refuses (and is caught as persistently divergent on the next audit pass, flagged in the trust ledger)

This means every reader who notices a gap is also repairing it. The network drifts toward completeness over time — seconds to minutes, depending on how many clients are active.

### 9.5 Fraud Proofs

A fraud proof is a standalone object (not an event in the DAG) published when a client catches a relay misbehaving. It contains:

```json
{
  "type":      "fraud_proof",
  "group":     "<group pubkey hex>",
  "relay":     "<relay pubkey hex>",
  "event_id":  "<event id hex>",
  "event":     { ... },
  "receipt":   { ... },
  "evidence":  "<description of the contradiction: e.g. attestation hash, not_found response, etc.>"
}
```

The proof is self-contained: the event is signed by its author (proving it's real), the receipt is signed by the relay (proving the relay received it), and the evidence shows the relay doesn't serve it. Any third party can verify all of this independently without trusting the publisher of the fraud proof.

Fraud proofs are published to relays (for storage and gossip) or shared out-of-band. They are not part of the DAG — they are evidence about relay behavior, not about group history.

Uses local trust ledgers for relay reputation. Cross-client reputation propagation (fraud proofs as first-class gossiped objects with network-wide effect) is deferred.

### 9.6 What This Catches — and What It Doesn't

**Caught (cryptographically, against 1-2 bad relays):**

- A relay cannot forge, alter, or insert events. (Signatures + DAG parents.)
- A relay cannot silently drop an event it signed a receipt for, then omit from its attestation. (Receipt + attestation divergence is provable to any observer.)
- Split-view attacks are provable when caught. (Signed attestations can be compared across clients.)
- Relays that lag behind are detected via attestation comparison and the network self-heals via backfill.
- Censorship surface grows over time via the DAG: censoring a message requires censoring all its descendants, transitively.

**Caught (probabilistically):**

- Split-view attacks are mitigated by clients connecting from varied vantage points. Not perfect; same model as Certificate Transparency.

**Not caught (fundamental):**

- If **all** relays a client uses collude to suppress a message **and** all its descendants, the client has no signal. You cannot prove a negative without an honest witness.
- A relay could refuse to **accept** a message at all (no receipt created). The author's mitigation is multi-relay redundancy: publish to K=3 relays; if at least one is honest, it signs a receipt and serves the event. The silent refusal of the others is provable by attestation divergence (honest relay has the event, dishonest one doesn't).

**The trap for bad relays:** to look like a functioning relay (and avoid immediate detection), a bad relay has to accept events and issue receipts. Once it does, receipts + attestations make subsequent omission provable. If it refuses to accept at all, it's immediately divergent from honest relays that do have the event. Either way, 1-2 bad relays in a curated set are caught without affecting user experience — the other K-1 or K-2 honest relays serve the events.

---

## 10. Protocol Event Types

### 10.1 Protocol-Level Types

These are defined by the protocol specification. They are bare strings without a dot. They handle group lifecycle, membership, moderation, and infrastructure.

| Type | Author | Content | Description |
|---|---|---|---|
| `genesis` | Group key | `{name, description, public, founder, mods, relays}` | Creates a new group. Only event signed with the group private key. The `author` field contains the founder's user pubkey. |
| `join` | User (self) | `{}` | User joins the group. In private groups, requires a prior `invite`. |
| `leave` | User (self) | `{}` | User leaves the group. |
| `invite` | Mod | `{invitee, role}` | Invites a user to the group. Required before `join` in private groups. |
| `kick` | Mod | `{target}` | Removes a user from `joined`. User can re-join. Does not ban. |
| `ban` | Mod | `{target, until, reason}` | Bans a user. `until` is optional (null = permanent). `reason` is a string. Prevents re-joining until `unban`. |
| `unban` | Mod | `{target}` | Lifts a ban. |
| `mod_add` | Mod | `{target}` | Promotes a member to moderator. |
| `mod_remove` | Mod | `{target}` | Demotes a moderator to regular member. |
| `relay_update` | Mod | `{relays: [...]}` | Updates the canonical relay list. |
| `metadata_update` | Mod | `{name, description}` | Updates group name or description. |

### 10.2 The `chat` Namespace (Default App)

FERN defines an official default app namespace, `chat`, which covers basic group chat features. This is the canonical set of event types that any FERN-compatible chat client should support. Other namespaces can be built on top of FERN for different applications (polls, scheduling, collaborative boards, etc.).

| Type | Content | Description |
|---|---|---|
| `chat.message` | `{text, channel, reply_to?}` | A chat message. `channel` scopes to a channel within the group. `reply_to` is an optional event ID for threaded replies. |
| `chat.reaction` | `{target, emoji}` | A reaction to a message. |
| `chat.nickname_set` | `{nickname}` | Self-asserted nickname for the signing user. |

Apps are free to define additional namespaces and types. Unknown types are ignored by clients that don't understand them — forward compatibility is free.

---

## 11. Relays

### 11.1 Role

Relays are storage and forwarding infrastructure. They have no authority over group state, membership, or identity. A relay cannot:

- **Forge events** — all events are cryptographically signed by authors
- **Rewrite events** — modifying content breaks the signature
- **Silently censor** — gaps in the DAG are visible, attestations commit to known sets, receipts prove receipt
- **Control the group** — authority is derived from the DAG, not from the relay

A relay can only choose not to host a group, or to lag behind. Both are detectable and recoverable.

### 11.2 Canonical Relays

Each group has a set of **canonical relays** defined in current group state (via genesis and `relay_update` events). All new events are published to all canonical relays. All group history must be present on all canonical relays at all times.

The invariant: at every point in time, all current canonical relays hold identical complete history from genesis. This gives clients a clean symmetric comparison property — if all canonical relays agree on the same `set_hash`, `tips`, and `count`, the client has complete history.

Anyone can stand up a new relay for an existing group at any time: fetch the log from existing relays, start serving. The founder/mods can bless it (via `relay_update`) or not. Clients decide which relays to trust based on their own policies and audit results.

### 11.3 Relay Validation

When a relay receives an event it must:

1. Verify the event `id` matches the SHA-256 of the canonical serialisation
2. Verify the signature is valid for the `author` pubkey
3. Store the event if it belongs to a group the relay is hosting
4. Return a signed receipt to the publishing client

Relays must store events regardless of whether they hold the parent events. Relays must not apply authorisation rules — that is the client's responsibility. Relays store all events with valid signatures and valid structure, including events that clients would reject (e.g., a non-mod attempting to kick a user).

### 11.4 Relay Garbage Collection

Because relays store all events with valid signatures (including invalid admin actions that no client will honour), storage can grow with spam. The GC rule addresses this:

**Rule:** When a relay holds more than **N** events in a group, it may delete events that are tips (have no children) and have not been referenced as a parent by any of the subsequent N events.

Key properties:
- The relay does not need to understand event authorisation — it only tracks whether an event was used as a parent by any subsequent event
- An event is only GC'd if the DAG grew by N events without referencing it
- Events that are legitimately old tips (e.g., the last message in a quiet group) are also eligible for GC after the threshold
- Clients must never GC from their local cache — this rule applies only to relays
- Canonical relays should set N high enough (suggested default: N = 1000) that by the time an event is GC'd, it is extremely unlikely any honest client still needs it for gap healing

**Important:** GC'd events on one relay are still present on other canonical relays and in client local caches. GC is a per-relay storage optimization, not a history deletion. If a relay GC's an event that is later needed, it can be re-fetched from sibling relays or clients.

### 11.5 WebSocket API

Relays expose a WebSocket interface. Core actions:

**Subscribe to a group** (receives new events + attestation pushes):
```json
{"action": "subscribe", "group": "<group pubkey>"}
```

**Publish an event** (relay validates, stores, returns receipt):
```json
{"action": "publish", "event": { ... }}
```

**Request a specific event by ID:**
```json
{"action": "get", "id": "<event id>"}
```

**Sync all events for a group since a timestamp:**
```json
{"action": "sync", "group": "<group pubkey>", "since": 1711234567}
```

**Request current attestation:**
```json
{"action": "attestation", "group": "<group pubkey>"}
```

Relay responses:
```json
{"type": "event", "event": { ... }}
{"type": "receipt", "receipt": { ... }}
{"type": "attestation", "attestation": { ... }}
{"type": "not_found", "id": "<event id>"}
{"type": "error", "message": "..."}
```

Attestations are pushed to subscribed clients periodically (every ~5 seconds or every ~100 events, configurable per relay) without requiring a request.

---

## 12. Public vs Private Groups

### 12.1 Public Groups

In a public group (`public: true` in `genesis`), any user may publish a `join` event freely without a prior `invite`. Relays accept and store events from any author. Clients display the full event history to all viewers. This is the primary use case — Discord-like public servers.

### 12.2 Private Groups

In a private group (`public: false` in `genesis`), a user must have a `invite` event from a mod before they can publish a `join` event. The group address must be shared privately. Clients must reject `join` events from users not in `members`.

---

## 13. Discovery and Migration

### 13.1 Group Address

A group address is the canonical way to share a group reference:

```
<group_pubkey>@<relay1>,<relay2>,<relay3>
```

The relay hints are starting points only. Once a client has received any events, it derives the authoritative relay list from the signed group state in the DAG (`relays` field in group state, updated via `relay_update` events).

### 13.2 Initial Discovery

1. Client receives a group address out-of-band (invite link, shared URL, etc.)
2. Client connects to the hint relays
3. Client fetches the `genesis` event, verifies the group key signature
4. Client derives the authoritative relay list from the genesis content
5. Client connects to all canonical relays

### 13.3 Migration Procedure

To migrate a group to a new relay set:

1. A mod publishes a `relay_update` event naming the new relay set
2. All connected clients observe the update and seed the new relays with full history (Section 13.4)
3. All clients begin publishing new events to the new relay set
4. Old relays must not be decommissioned until every new relay holds the complete history — clients confirm this by cross-referencing event sets across old and new relays before the old set is removed

The invariant is that at every point in time, all current canonical relays hold identical complete history from genesis.

### 13.4 New Relay Seeding

When a client observes a `relay_update` event adding a new relay to the canonical list, it must:

1. Connect to the new relay
2. Walk its full local history for the group
3. Publish any events the new relay reports as not found

Clients must perform this seeding before sending new messages to the group. The migration is not complete until all canonical relays hold identical history.

---

## 14. Client Behaviour

### 14.1 Local Cache

Clients must persist their full local event history to disk. This cache is essential for gap healing and relay seeding. Clients must never evict events from their local cache unless explicitly instructed by the user. The local cache is also where receipts are stored (author-local).

### 14.2 Joining a Group

1. Receive a group address out-of-band
2. Connect to hint relays, fetch and verify the `genesis` event
3. Derive the canonical relay list from genesis, connect to all canonical relays
4. Sync from all canonical relays using the `sync` action; merge results
5. Verify completeness: if all canonical relays agree on the same `set_hash`, `tips`, and `count`, the client has the full history. If not, fetch and republish differences (backfill).
6. Walk the DAG from genesis:
   - Verify signatures and parent references
   - Compute the genesis-connected event set
   - Apply connected state events to compute current group state
   - Store disconnected events as pending/gappy and request missing parents via gap healing
7. Open live subscriptions on all canonical relays
8. Begin running the monitor audit pass in the background

### 14.3 Publishing Events

1. Construct the event with `parents` referencing the latest known connected heads in the group
2. Sign the event with the author's user private key
3. Publish to all canonical relays **simultaneously and in parallel**
4. Collect receipts from each relay
5. Store receipts locally alongside the event in the local cache
6. Cache the event locally

Locally cached events that have not been accepted by any canonical relay are retryable local sends, not heads. If delivery fails, the client should show a retry affordance and exclude the failed event from parent selection so later messages do not build on it.

Publishing to fewer than all canonical relays is strongly discouraged. Message loss resulting from single-relay publishing is the sender's responsibility. A message is considered "safely acknowledged" when receipts from at least 2 relays are collected (configurable).

### 14.4 Receiving

Clients maintain persistent WebSocket connections to all canonical relays for each group. Messages from whichever relay delivers them first are accepted. Duplicates are deduplicated by event ID.

### 14.5 Live Monitoring

After initial sync, the client continuously:

- **Receives new events**: verify, store, recompute connectedness, trigger gap healing for disconnected events, and render connected events per client policy (hide banned/unauthorised if configured).
- **Receives attestation pushes**: run the monitor audit pass (Section 9.3). Compare relay state to local known-set and to other relays. Trigger backfill for missing events. Flag persistent divergence in local trust ledger.
- **Maintains a local trust ledger**: `{relay_pubkey → {observed_faults, last_attestation}}`. This is local state, not network-consensus. Different clients may have different views of which relays are trustworthy.

Trust propagation is **social, not protocol-enforced**. The protocol does not dictate that faulted relays are de-listed across clients. The protocol guarantees misconduct is *provable*; the social response (drop relay R from your config) is up to the client/operator.

### 14.6 Displaying Gaps

When rendering group history, clients must visibly indicate known gaps — event IDs referenced as parents that are not present in the local cache. Gaps must not be silently hidden. Disconnected child events should be shown only as pending/gap diagnostics, not as ordinary chat messages, until the missing parent chain is healed and the events become connected to genesis.

---

## 15. Protocol vs App Boundary

The protocol (FERN) defines:
- Event structure and canonical serialisation
- Cryptographic primitives (keys, hashing, signing)
- Identity (user, group, relay)
- The causal DAG (parents, heads, gaps, healing)
- Group state machine (membership, moderation, relays, metadata)
- Completeness layer (receipts, attestations, monitoring, backfill, fraud proofs)
- Relay protocol (WebSocket actions, relay validation, GC)
- Discovery and migration
- Protocol-level event types (genesis, join, leave, invite, kick, ban, unban, mod_add, mod_remove, relay_update, metadata_update)

Applications define:
- Their own event types (`appname.local_type`)
- Content schemas for those types
- How events are displayed and interacted with
- App-specific state derived from events (e.g., a poll app tracks vote tallies; a chat app tracks channels and threads)

The protocol is agnostic to app-level types. It transports, stores, signs, and provides completeness guarantees for all events uniformly, regardless of type. Unknown types are stored by relays and ignored by clients that don't understand them.

This separation lets multiple apps share the same infrastructure: identity, relays, completeness guarantees, group management. A new app is just a new set of event types and a client that understands them.

---

## 16. Threat Model Summary

| Attack | Protection |
|---|---|
| Forge a message as another user | Author signatures defeat this |
| Forge a group state change (admin action) | Mod signatures + check against folded mod list |
| Insert a fake event into the DAG | Signatures prevent forgery; the connected-subgraph rule prevents disconnected events from entering state, normal rendering, or future parent selection |
| Relay silently censors a message it accepted | Receipt + attestation divergence; provable to any observer via fraud proof |
| Relay refuses to accept a message at all | Multi-relay redundancy (K=3); if at least one honest, it has the event and the others are provably divergent |
| Split-view attack (relay serves different state to different clients) | Signed attestations make divergence provable when clients compare notes. Defeated probabilistically by vantage diversity. |
| Relay shuts down | Group continues on remaining relays; new relays can stand up anytime |
| Relay lags behind | Detected via attestation comparison; self-healed via backfill from sibling relays |
| Founder issues bad bans/mod decisions | Founder trust is accepted at join time. Bans are render filters; underlying log stays complete. |
| All relays a client uses collude to suppress a message and its descendants | Censorship undetectable. Fundamental; out of scope without consensus. |
| IP exposure between members | No client-to-client connections; only relays see client IPs |

### The trap for 1-2 bad relays

In a curated set of K=3 trusted relays where 1-2 go bad:

- To avoid immediate detection, the bad relay must accept events and look operational.
- Once it accepts an event, it signs a receipt — committing to having received it.
- If it later omits the event, the receipt + attestation divergence is provable censorship.
- If it refuses to accept at all, it's immediately divergent from the K-1 or K-2 honest relays that do have the event.
- User experience is unaffected: the honest relays serve all events, and the bad relay is flagged and de-listed.

This is the core guarantee: **1-2 misbehaving relays in a curated set are caught without affecting user experience**.

---

## 17. Future Extensions

Documented future extensions, deliberately excluded to keep the design implementable:

- **Threshold founder signing**: multiple admin keys required for high-sensitivity actions. Founder single-key currently.
- **Merkle exclusion proofs**: allowing third parties to verify non-inclusion without holding the full set. Simple sorted-set-hash attestations currently.
- **Fork-proofs as first-class gossiped objects**: cross-client reputation propagation for relay misbehavior. Local trust ledgers currently.
- **Snapshots**: founder-signed state anchors to bound new-joiner cost for large groups. Not needed for small-to-medium groups (verify from genesis is fast enough). Will be added when groups reach scaling pain.
- **App-prefixed types with pubkey namespaces**: `<app_pubkey>.appname.type` for collision-free type names. Convention-based `appname.type` currently.
- **Protocol versioning**: a `protocol` field in genesis content for forward compatibility.
- **Relay-side policy enforcement**: relays checking group-state policies on ingest. Currently, relays accept any well-formed event; all moderation is client-side.
- **Extended app surfaces**: pins, channel policies, roles beyond member/mod, message edits. Apps can define these in their own event types.

These are documented as upgrade paths, not abandoned. The current design's goal is a working, secure, minimal protocol. Each deferred feature has a clear path back when concrete demand justifies the complexity.

---

## 18. The Whole Picture in One Paragraph

Signed events in a public append-only log, replicated across multiple interchangeable canonical relays that anyone can run. Events reference recent connected heads, forming a causal DAG that propagates existence proofs for free through normal chat activity without letting disconnected events poison future history. Relays sign receipts when they accept events, committing to having received them; relays periodically sign attestations committing to their full known set. Every client cross-checks relays against each other for free — monitors aren't a separate role, they're a property of clients running the audit pass on every attestation push. Backfill self-heals gaps automatically: any reader who notices a gap fetches the missing event and republishes it. Admin actions like bans are themselves events in the log, never deletions, so moderation is a render-time filter rather than a storage-level operation. The protocol is general-purpose: it handles transport, identity, group management, and completeness; applications define their own event types and content schemas on top. The result is a protocol for public group chats that can't be forged, can't be silently censored (provably if attempted by 1-2 bad relays in a curated set), can't be shut down, and hides member IPs by virtue of never connecting members directly.
