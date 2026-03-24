# FERN — Fault-tolerant Event Relay Network
## Protocol Specification v0.1

---

## 1. Overview

FERN (Fault-tolerant Event Relay Network) is a decentralised group messaging protocol. Identity and groups are cryptographic keypairs, not server accounts. All content is expressed as signed, immutable events arranged in a hash-linked Directed Acyclic Graph (DAG). Relays are interchangeable infrastructure with no authority over group state, membership, or identity.

### 1.1 Design Goals

- User identity is portable and not tied to any server
- Group identity is not owned or controlled by any relay
- No relay can forge, rewrite, or silently censor messages
- Groups survive relay failures and migrations
- History is self-healing through normal client behaviour
- The protocol is suitable for large public group chats

### 1.2 Non-Goals

- End-to-end encryption (FERN is designed primarily for public groups)
- Guaranteed message delivery
- Serverless or peer-to-peer transport
- Complete message history guarantee

---

## 2. Cryptographic Primitives

### 2.1 Keypairs

FERN uses **Ed25519** keypairs throughout.

- Private keys are 32 bytes
- Public keys are 32 bytes, encoded as lowercase hex
- Signatures are 64 bytes, encoded as lowercase hex

### 2.2 Hashing

All content addressing uses **SHA-256**, encoded as lowercase hex.

### 2.3 Event ID

The ID of an event is the SHA-256 hash of its canonical serialisation (see Section 4.3).

---

## 3. Identity

### 3.1 User Identity

A user identity is an Ed25519 keypair generated locally on the user's device.

```
privkey   32 bytes    never transmitted, never leaves the device
pubkey    32 bytes    the user's permanent global identifier
```

There is no registration process. A user exists as soon as they generate a keypair. Identity is not tied to any relay or server.

### 3.2 Key Representation

Public keys are represented as lowercase hex strings:

```
9e7b4a2f1c3d8e5f6a0b2c4d7e9f1a3b5c7d9e0f2a4b6c8d1e3f5a7b9c2d4e6f
```

---

## 4. Events

Everything in FERN is an event. Messages, group creation, invites, joins, membership changes, and relay list updates are all expressed as events in the same format.

### 4.1 Event Structure

```json
{
  "id":       "<sha256 of canonical serialisation>",
  "type":     "<event type string>",
  "group":    "<group pubkey hex>",
  "author":   "<author pubkey hex>",
  "parents":  ["<event id hex>", ...],
  "content":  "<event type specific content>",
  "ts":       1711234567,
  "sig":      "<ed25519 signature hex>"
}
```

### 4.2 Field Definitions

| Field | Type | Description |
|---|---|---|
| `id` | hex string | SHA-256 of canonical serialisation |
| `type` | string | Event type identifier (see Section 5) |
| `group` | hex string | Public key of the group this event belongs to |
| `author` | hex string | Public key of the event author |
| `parents` | array of hex | IDs of parent events in the DAG |
| `content` | object or string | Event type specific payload |
| `ts` | integer | Unix timestamp in seconds |
| `sig` | hex string | Ed25519 signature (see Section 4.4) |

### 4.3 Canonical Serialisation

The canonical serialisation used for hashing and signing is a JSON array with fields in a fixed order, with no whitespace:

```json
[type, group, author, parents, content, ts]
```

- `parents` is sorted lexicographically before serialising
- `content` is serialised as a JSON object if structured, or a plain string if text
- No trailing whitespace or newlines

### 4.4 Signing

The signature covers the canonical serialisation:

```
sig = Ed25519Sign(privkey, SHA256(canonical_serialisation))
```

The `id` field is:

```
id = SHA256(canonical_serialisation)
```

### 4.5 Verification

To verify an event a client must confirm:

1. The `id` matches the SHA-256 of the canonical serialisation
2. The `sig` is a valid Ed25519 signature over the canonical serialisation, verifiable with the `author` pubkey
3. The `author` is authorised to publish this event type at this point in the DAG (see Section 6)

---

## 5. Event Types

### 5.1 `group_genesis`

Creates a new group. Must be the first event in any group's DAG. Signed by the group private key.

```json
{
  "type": "group_genesis",
  "author": "<founder user pubkey>",
  "content": {
    "name":        "Group Name",
    "description": "Optional description",
    "public":      true,
    "founder":     "<founder user pubkey>",
    "mods":        ["<founder user pubkey>"],
    "relays":     ["<relay url>", ...]
  },
  "parents": [],
  "sig": "<signed with GROUP privkey>"
}
```

Note: This is the only event type signed with the group private key rather than the author's user private key. The `author` field still contains the founder's user pubkey. Clients verify the `sig` against the group pubkey (which is also the `group` field).

The founder is automatically considered joined after genesis.

### 5.2 `message`

A chat message from a joined group member. Only users who have an active `group_join` (and no subsequent `group_leave` or `group_kick`) may post messages.

```json
{
  "type":    "message",
  "content": "Hello, world.",
  "parents": ["<id of most recent event(s) seen>"]
}
```

`parents` should reference the most recent event IDs the author had seen when composing the message. Multiple parents are valid and represent concurrent branches being merged.

### 5.3 `group_invite`

Invites a user to the group. In private groups, a user must be invited before they can join. Must be authored by a current mod.

```json
{
  "type": "group_invite",
  "content": {
    "invitee": "<user pubkey>",
    "role":    "member"
  }
}
```

### 5.4 `group_join`

A user joins the group. The author signs this event themselves. In private groups, a valid `group_invite` from a mod must exist before the join event. In public groups, any user may join freely.

```json
{
  "type": "group_join",
  "author": "<user pubkey>",
  "content": {},
  "parents": ["<id of most recent event(s) seen>"]
}
```

Once joined, the user appears in the members list and may post messages.

### 5.5 `group_leave`

A user leaves the group. The author signs this event themselves. After leaving, the user can no longer post messages until they join again.

```json
{
  "type": "group_leave",
  "author": "<user pubkey>",
  "content": {},
  "parents": ["<id of most recent event(s) seen>"]
}
```

### 5.6 `group_kick`

Removes a user from the group. The kicked user is removed from the joined and member lists. Must be authored by a current mod.

```json
{
  "type": "group_kick",
  "content": {
    "target": "<user pubkey>"
  }
}
```

### 5.7 `mod_add`

Promotes a member to moderator. Must be authored by a current mod.

```json
{
  "type": "mod_add",
  "content": {
    "target": "<user pubkey>"
  }
}
```

### 5.8 `mod_remove`

Demotes a moderator to regular member. Must be authored by a current mod.

```json
{
  "type": "mod_remove",
  "content": {
    "target": "<user pubkey>"
  }
}
```

### 5.9 `relay_update`

Updates the canonical relay list. Must be authored by a current mod.

```json
{
  "type": "relay_update",
  "content": {
    "relays": ["<relay url>", ...]
  }
}
```

### 5.10 `group_metadata`

Updates group name or description. Must be authored by a current mod.

```json
{
  "type": "group_metadata",
  "content": {
    "name":        "New Name",
    "description": "New description"
  }
}
```

---

## 6. Group State

Group state is derived entirely by replaying events in the DAG in timestamp order. There is no separate state database. Any relay or client with the full event history will derive identical state.

### 6.1 State Model

```
members:   set of pubkeys invited to the group
joined:    set of pubkeys who have joined (have an active group_join)
mods:      set of pubkeys with moderator privileges
relays:    [url, url, ...]
metadata:  { name, description }
```

### 6.2 Derivation Rules

Starting from genesis:

- `members` is initialised with the founder pubkey
- `joined` is initialised with the founder pubkey (the founder is automatically joined)
- `mods` is initialised with the founder pubkey
- `relays` is initialised from the genesis content
- `metadata` is initialised from the genesis content

For each subsequent event in timestamp order:

| Event type | State change |
|---|---|
| `group_invite` | Add `invitee` to `members` |
| `group_join` | Add `author` to `joined` |
| `group_leave` | Remove `author` from `joined` |
| `group_kick` | Remove `target` from `joined` and `mods` (keep in `members`) |
| `mod_add` | Add `target` to `mods` |
| `mod_remove` | Remove `target` from `mods` |
| `relay_update` | Replace `relays` with new value |
| `group_metadata` | Replace `metadata` fields present in content |

### 6.3 Conflict Resolution

When two events have the same timestamp and affect the same state field, the event with the lexicographically greater ID wins. This rule is deterministic and produces the same result on all clients regardless of event arrival order.

### 6.4 Authorisation

Before applying a state change event, clients verify:

- The `author` pubkey is in `mods` at the point in the DAG immediately before this event
- Exception: `group_genesis` is verified against the group pubkey
- Exception: `group_join` and `group_leave` are verified against the `author` pubkey directly (users can always join or leave for themselves)

### 6.5 Join Validation

When applying a `group_join` event:

- In **public groups** (`public: true`): any user may join freely
- In **private groups** (`public: false`): the `author` must be in `members` (i.e., must have a prior `group_invite` from a mod). If not in `members`, the join event is discarded.

Events failing authorisation or join validation are discarded silently. Their IDs remain valid as parent references.

---

## 7. Posting

Only users in the `joined` set may publish `message` events. Messages from users not in `joined` are discarded by clients. Relays do not enforce this — it is the client's responsibility.

Users can view group history and state without joining. Joining is required only to post messages.

---

## 8. The DAG

### 8.1 Structure

Events form a Directed Acyclic Graph where edges are parent references. Every event except `group_genesis` must have at least one parent. The genesis event has an empty `parents` array and is the root of the DAG.

### 8.2 Branches and Merges

Concurrent events — those written without knowledge of each other — naturally create branches. Branches are resolved when a later event references both tips as parents, merging them. Clients display branched history using timestamp ordering.

### 8.3 Gaps

If a client has an event whose parent hash is unknown, that parent is considered a gap. Gaps are:

- Visible to clients — the missing hash is known
- Specifically addressable — a client can request the missing event by its exact hash
- Not fatal — events after a gap are stored and displayed, with the gap marked as missing in the UI

Clients must never discard events solely because their parents are absent.

### 8.4 Gap Resolution

When a client detects a missing parent hash it should:

1. Request the event by ID from every relay on the current canonical active relay list
2. If found, verify and store it
3. If found on some relays but not others, publish it to the relays that were missing it

---

## 9. Relays

### 9.1 Role

Relays are storage and forwarding infrastructure. They have no authority over group state, membership, or identity. A relay cannot:

- Forge events — all events are cryptographically signed
- Rewrite events — modifying content breaks the signature
- Silently censor — gaps in the DAG are visible to clients
- Control the group — authority is derived from the DAG, not from the relay

A relay can only choose not to host a group. This is detectable and recoverable.

### 9.2 Relay Types

FERN has a single relay type: canonical relays. These are the authoritative set of relays for a group at any given time, defined in current group state. All new events are published to all canonical relays. All group history must be present on all canonical relays at all times.

### 9.3 Relay Validation

When a relay receives an event it must:

1. Verify the event ID matches the SHA-256 of the canonical serialisation
2. Verify the signature is valid for the `author` pubkey
3. Store the event if it belongs to a group the relay is hosting

Relays must store events regardless of whether they hold the parent events.

Relays must not apply authorisation rules — that is the client's responsibility.

### 9.4 WebSocket API

Relays expose a WebSocket interface.

**Subscribe to a group:**
```json
{ "action": "subscribe", "group": "<group pubkey>" }
```

**Publish an event:**
```json
{ "action": "publish", "event": { ... } }
```

**Request a specific event by ID:**
```json
{ "action": "get", "id": "<event id>" }
```

**Request all events for a group since a given timestamp:**
```json
{ "action": "sync", "group": "<group pubkey>", "since": 1711234567 }
```

**Request a completeness summary for cross-relay verification:**
```json
{ "action": "summary", "group": "<group pubkey>" }
```

Relays respond to `summary` with:
```json
{ "type": "summary", "group": "<group pubkey>", "count": 1452, "tips": ["<event id>", ...] }
```

`tips` is the set of event IDs that have no children — the current frontier of the DAG. `count` is the total number of stored events. Clients compare summaries across all canonical relays to detect divergence cheaply without transferring the full history.

Relays respond with:
```json
{ "type": "event", "event": { ... } }
{ "type": "not_found", "id": "<event id>" }
{ "type": "ok", "id": "<event id>" }
{ "type": "error", "message": "..." }
```

---

## 10. Group Address

A group address is the canonical way to share a group reference:

```
<group_pubkey>@<relay1>,<relay2>,<relay3>
```

Example:

```
9e7b4a2f...@relay-a.example.com,relay-b.example.com,relay-c.example.com
```

The relay hints are starting points only. Once a client has received any events, it derives the authoritative relay list from the signed group state in the DAG.

---

## 11. Client Behaviour

### 11.1 Publishing

When sending an event, clients must:

1. Construct the event with parents referencing the latest known event IDs in the group
2. Sign the event
3. Publish to all canonical relays **simultaneously and in parallel**
4. Cache the event locally

Publishing to only one relay is strongly discouraged. Message loss resulting from single-relay publishing is the sender's responsibility.

### 11.2 Receiving

Clients maintain persistent WebSocket connections to all canonical relays for each group. Messages from whichever relay delivers them first are accepted. Duplicates are deduplicated by event ID.

### 11.3 History Fetching

When joining a group or reconnecting after absence, clients sync from all canonical relays using the `sync` action and merge the results. Completeness is verified by cross-referencing all canonical relays — if all return identical event sets, the client has the full history. If one relay has events another lacks, the client fetches the difference and republishes it to the relay that was missing it.

### 11.4 Gap Healing

When a client detects a missing parent it must follow the procedure in Section 8.4. After fetching a missing event from one canonical relay, the client must republish it to any canonical relays that reported it as not found.

### 11.5 New Relay Healing

When a client observes a `relay_update` event adding a new relay to the canonical list, it must:

1. Connect to the new relay
2. Walk its full local history for the group
3. Publish any events the new relay reports as not found

Clients must perform this seeding before sending new messages to the group. The migration is not complete until all canonical relays hold identical history.

### 11.6 Local Cache

Clients must persist their full local event history to disk. This cache is essential for gap healing and relay seeding. Clients must never evict events from their local cache unless explicitly instructed by the user.

### 11.7 Displaying Gaps

When rendering group history, clients must visibly indicate known gaps — event IDs referenced as parents that are not present in the local cache. Gaps must not be silently hidden.

### 11.8 Relay Garbage Collection

Relays store all events with valid signatures, regardless of authorisation. This means invalid events (e.g. a non-mod attempting to kick a user) will be stored by relays but rejected by all clients. These invalid events are never referenced as parents by valid clients, so the DAG continues to grow from valid events without referencing them.

**GC Rule:** When a relay holds more than **N** events in a group, it may delete events that are tips (have no children) and have not been referenced as a parent by any of the subsequent N events.

**Key properties:**
- The relay does not need to understand event authorisation — it only tracks whether an event was used as a parent
- An event is only GC'd if the DAG grew by N events without referencing it
- Events that are legitimately old tips (e.g. the last message in a quiet group) are also eligible for GC after the threshold
- Clients must never GC from their local cache — this rule applies only to relays

**Configurable threshold N** should be chosen so that by the time an event is GC'd, it is extremely unlikely any honest client still needs it for gap healing. A suggested default is N = 100.

---

## 12. Relay Discovery and Migration

### 12.1 Initial Discovery

The group address (Section 10) provides initial relay hints. Clients attempt connection to all relays in the hint list and fetch the group genesis event to establish the authoritative relay list from group state.

### 12.2 Migration

To migrate a group to a new relay set:

1. A mod publishes a `relay_update` event naming the new relay set
2. All connected clients observe the update and seed the new relays with full history (Section 11.5)
3. All clients begin publishing new events to the new relay set
4. Old relays must not be decommissioned until every new relay holds the complete history — clients confirm this by cross-referencing event sets across old and new relays before the old set is removed

The invariant is that at every point in time, all current canonical relays hold identical complete history from genesis.

---

## 13. Public vs Private Groups

### 13.1 Public Groups

In a public group, `public: true` in genesis. Relays accept and store events from any author. Any user may publish a `group_join` event freely without a prior invite. Clients display the full event history to all viewers.

### 13.2 Private Groups

In a private group, `public: false` in genesis. A user must have a `group_invite` event from a mod before they can publish a `group_join` event. The group address must be shared privately. Clients must reject `group_join` events from users not in `members`.

---

## 14. Limitations and Honest Tradeoffs

### 14.1 Completeness Is Provable

Because all canonical relays must hold identical complete history at all times, a client can prove it has received all messages by cross-referencing the event sets returned by all canonical relays. If all canonical relays agree on the same DAG — same event IDs, same tips, same count — the client has the complete history. Disagreement between canonical relays is itself a signal to fetch and republish the difference. This is the core guarantee that distinguishes FERN from protocols where completeness cannot be verified.

### 14.2 History Preservation During Migration

The completeness guarantee holds only while migration discipline is followed. If a relay is decommissioned before all new canonical relays have been fully seeded, history on that relay is lost. The protocol requires — but cannot enforce — that mods and clients follow the migration procedure in Section 12.2 before dropping old relays.

### 14.3 Censorship Detectability

A relay can withhold events. This censorship is always detectable as a visible gap in the DAG. Because multiple canonical relays must hold the same history, a gap on one relay that is absent on others is immediately detectable and healable by any client.

### 14.4 Group Key Security

The group private key is the root of trust for group genesis. If the group private key is compromised, an attacker can publish a fraudulent genesis. The group private key should be treated as a high-value secret, stored offline, and used only once at group creation.

---

## 15. Versioning

This document describes FERN protocol version `0.1`. The genesis event does not currently include a protocol version field. A future revision should add a `protocol` field to `group_genesis` content to allow forward compatibility.

---

*FERN Protocol Specification v0.1 — Draft*
