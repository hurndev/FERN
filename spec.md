# FERN — Protocol Specification

This document is the normative specification of the FERN protocol. It defines the wire formats, cryptographic primitives, event structures, group state machine, completeness layer, and relay protocol required for two independent implementations to interoperate.

Conformance keywords (MUST, MUST NOT, SHOULD, MAY) are used per RFC 2119.

---

## 1. Cryptographic Primitives

### 1.1 Ciphersuites

FERN uses the following primitives throughout. No alternatives are negotiated.

| Purpose | Algorithm |
|---|---|
| Signing | Ed25519 (RFC 8032) |
| Hashing | SHA-256 (FIPS 180-4) |

### 1.2 Key Formats

All Ed25519 keypairs consist of:
- **Private key**: 32 bytes (the seed)
- **Public key**: 32 bytes
- **Signature**: 64 bytes

Public keys, private keys (when stored), and signatures are encoded as **lowercase hexadecimal strings**.

Examples below use unbracketed placeholders like `<pubkey_hex>` to refer to 64-character lowercase hex strings.

### 1.3 Hash Encoding

All SHA-256 outputs are encoded as **lowercase hexadecimal strings** (64 characters).

---

## 2. Identity

### 2.1 User Identity

A user identity is a locally-generated Ed25519 keypair. There is no registration process. The user's public key is their global identifier.

### 2.2 Group Identity

A group has its own Ed25519 keypair, distinct from the founder's user keypair. The group's public key **is** the group's identifier.

The group private key MUST be used only to sign the `genesis` event. It MUST NOT be used for any other purpose. After signing the genesis event, the group private key SHOULD be stored offline or destroyed (it is not needed for ongoing operations; subsequent group modifications are signed by mods' user keys).

### 2.3 Relay Identity

A relay has its own Ed25519 keypair, used for signing receipts and attestations. Clients learn the relay's public key via the relay metadata endpoint (Section 10.6) over TLS on first connection and MUST store it for subsequent verification.

### 2.4 Hex Encoding Rules

Unless otherwise specified:
- Public keys, event IDs, signatures, hashes, and similar binary data are encoded as lowercase hex strings.
- Hex strings MUST be even-length and contain only characters `[0-9a-f]`.
- Implementations MUST reject malformed hex inputs.

---

## 3. Events

### 3.1 Event Structure

An event is a JSON object with exactly these fields:

```json
{
  "id":       "<event id hex>",
  "type":     "<event type string>",
  "group":    "<group pubkey hex>",
  "author":   "<author pubkey hex>",
  "parents":  ["<event id hex>", ...],
  "content":  { ... },
  "ts":       1711234567,
  "tags":     [],
  "sig":      "<ed25519 signature hex>"
}
```

### 3.2 Field Constraints

| Field | Type | Constraints |
|---|---|---|
| `id` | string | 64-char lowercase hex (SHA-256 output). MUST equal `compute_id(event)` (Section 3.4). |
| `type` | string | Non-empty. See Section 4 for namespacing rules. |
| `group` | string | 64-char lowercase hex (the group's public key). |
| `author` | string | 64-char lowercase hex (the author's user pubkey). For `genesis` events, this is the founder's user pubkey (not the group key). |
| `parents` | array of strings | Each element MUST be a 64-char lowercase hex event ID. For `genesis`, this array MUST be empty. For all other events, this array MUST contain at least one element. Elements MUST be unique within the array. |
| `content` | object | MUST be a JSON object (never a bare string, number, array, or null). Schema is determined by `type`. |
| `ts` | integer | Unix timestamp in seconds. MUST be a positive integer. |
| `tags` | array | Reserved for protocol-level extensions. Implementations MUST set this to `[]` if unused. Each element, when present, MUST be an array of strings. See Section 3.6. |
| `sig` | string | 128-char lowercase hex (Ed25519 signature). MUST be a valid signature over the canonical serialisation (Section 3.4). |

### 3.3 Canonical Serialisation

The canonical serialisation of an event is a UTF-8 encoded JSON array with fields in this exact order:

```
[type, group, author, parents, content, ts, tags]
```

Serialisation rules:

1. The output MUST be a JSON array (begins with `[`, ends with `]`).
2. Fields MUST appear in the order shown above.
3. No insignificant whitespace is permitted. The output MUST NOT contain spaces, tabs, or newlines except inside string values.
4. All string values MUST be escaped per RFC 8259 with the following additional constraints:
   - Use `\"` for quotes
   - Use `\\` for backslash
   - Use `\n`, `\r`, `\t` for control characters
   - Other control characters below U+0020 MUST be escaped as `\uXXXX`
5. `parents` MUST be sorted lexicographically (byte-wise comparison of the UTF-8 encoded hex strings) before serialisation.
6. `content` MUST be serialised as a JSON object. Object keys MUST be sorted lexicographically (byte-wise comparison of UTF-8 encoded keys). This sorting applies recursively to all nested objects.
7. Array values inside `content` are NOT sorted (they preserve their order, which may be semantically meaningful).
8. `tags` MUST be sorted: by the first element first, then the second element, and so on (lexicographic byte-wise comparison of UTF-8). This sort is stable across arrays of the same prefix.
9. `ts` is serialised as a JSON integer (no quotes, no decimals, no leading zeros except for `0` itself).
10. JSON booleans and `null` inside `content` or `tags` are permitted where allowed by their schema.
11. The serialised output MUST NOT have any trailing newline.

For example, given an event with:

```
type      = "chat.message"
group     = "abcd...1234"
author    = "0123...cdef"
parents   = ["fff...", "111...", "888..."]   // unsorted input
content   = {"text": "hi", "channel": "general"}  // unsorted keys
ts        = 1711234567
tags      = []
```

The canonical serialisation is (with `...` eliding real hashes for brevity):

```
["chat.message","abcd...1234","0123...cdef",["111...","888...","fff..."],{"channel":"general","text":"hi"},1711234567,[]]
```

### 3.4 Computing `id` and `sig`

Given the canonical serialisation bytes (UTF-8):

```
id  = lowercase_hex( SHA256( canonical_serialisation_bytes ) )
sig = lowercase_hex( Ed25519Sign( privkey, canonical_serialisation_bytes ) )
```

For `genesis` events, the signing key is the **group private key** (the key corresponding to the `group` field's public key). For all other events, the signing key is the **author's user private key** (the key corresponding to the `author` field's public key).

### 3.5 Verification Algorithm

To verify an event, an implementation MUST perform these checks in order:

1. **Structural validation**: all fields from Section 3.2 are present and conform to their type constraints (hex length, format, etc.). If invalid, reject as `malformed`.

2. **Hash check**: compute `compute_id(event)` (Section 3.4) and confirm it equals the `id` field. If not, reject as `invalid_hash`.

3. **Signature check**:
   - If `type == "genesis"`: verify `sig` is a valid Ed25519 signature over the canonical serialisation using the public key in the `group` field. The `group` field MUST be a valid pubkey.
   - Otherwise: verify `sig` is a valid Ed25519 signature over the canonical serialisation using the public key in the `author` field.
   - If invalid, reject as `invalid_signature`.

4. **Authorisation check** (state-dependent; see Section 8.5): for state-change events, verify the `author` is authorised to perform this action at this point in the DAG. If not, reject as `unauthorised` (but the event is still stored by relays — see Section 10.3).

Events that fail structural or hash or signature checks MUST NOT be stored by relays or included in client state. Events that fail authorisation are stored by relays but rejected from client state (see Section 10.3).

### 3.6 Tags

The `tags` field is reserved for protocol-level extensions. As of this specification, no tag names are defined. Implementations MUST set `tags` to `[]` when constructing events and MUST preserve any tags received (for forward compatibility).

App-specific data MUST NOT be placed in `tags`; it goes in `content`.

A tag is an array of strings. Tag names (the first element) follow the same namespacing rules as event types (Section 4): bare strings are protocol-reserved, dotted strings are extension-reserved.

---

## 4. Event Type Namespacing

### 4.1 Type String Grammar

A type string is one of:

- **Protocol type**: a non-empty string not containing the `.` character. Reserved for protocol-defined types (Section 5). Apps MUST NOT use bare (no-dot) type names.
- **App type**: a string containing at least one `.` separator. The first segment (before the first `.`) is the **namespace** identifier. Subsequent segments (after the first `.`) form the local type name.

Examples:
- `genesis`, `join`, `ban` — protocol types
- `chat.message`, `chat.reaction` — types in the `chat` namespace
- `poll.vote`, `schedule.event` — types in other namespaces

### 4.2 The `chat` Namespace

The `chat` namespace is the official default app namespace, maintained as part of FERN. It covers basic group chat features. Its event types are specified in Section 6.2.

Other namespaces (e.g., `poll`, `schedule`, `whiteboard`) can be defined by anyone building on FERN. Namespace collisions are resolved socially, like package names in npm.

### 4.3 Unknown Type Handling

The protocol treats all events uniformly for transport, validation, and completeness — it does not interpret `content`. Implementations MUST store and transport events of unknown types. Client applications SHOULD ignore unknown types when rendering (forward compatibility).

---

## 5. Protocol Event Types

These event types are defined by the protocol and handle group lifecycle, membership, moderation, and infrastructure. They are bare strings (no dot).

### 5.1 `genesis`

Creates a new group. MUST be the first event in any group's DAG. MUST be signed by the **group private key** (the key corresponding to the `group` field). The `author` field contains the founder's user pubkey.

The `parents` array MUST be empty.

Content schema:

```json
{
  "name":        "Group Name",
  "description": "Optional description",
  "public":      true,
  "founder":     "<founder user pubkey hex>",
  "mods":        ["<founder user pubkey hex>"],
  "relays":      ["wss://relay1.example.com", "wss://relay2.example.com"],
  "app":         "chat",
  "chat.channels": ["general"]
}
```

| Field | Type | Description |
|---|---|---|
| `name` | string | Human-readable group name. Non-empty. |
| `description` | string | Optional description. May be empty. |
| `public` | boolean | If `true`, any user may `join` freely. If `false`, `join` requires a prior `invite`. |
| `founder` | string | Founder's user pubkey (64-char hex). MUST equal the `author` field of this event. |
| `mods` | array of strings | Initial mod pubkeys. MUST include the founder. Initial list MUST be non-empty. |
| `relays` | array of strings | Initial canonical relay URLs (e.g., `wss://...`). MUST be non-empty. |
| `app` | string | The primary app namespace this group uses. All app-level event types MUST be prefixed with `app`. For example `"app": "chat"` means all app event types use the `chat.` prefix (e.g., `chat.message`). A client that does not understand `app` SHOULD NOT engage with the group. MUST be a non-empty string containing at least one `.` or recognised bare name (e.g., `"chat"`). |
| `chat.channels` | array of strings | Required when `app` is `"chat"`. Initial channel list. MUST contain at least one channel. MUST contain `"general"`. |

Verification: `sig` MUST verify against the `group` field as the public key. The `founder` field MUST equal the `author` field. The `mods` array MUST contain the `founder` pubkey. If `app == "chat"`, `chat.channels` MUST be present and non-empty. `chat.channels` MUST contain `"general"`.

### 5.2 `join`

A user joins the group. The author signs this event themselves.

Content schema:

```json
{}
```

(Empty object — no fields required at this time. Reserved for future extension.)

Authorisation: in public groups, any user may join. In private groups, the `author` MUST be in the group's `members` set (i.e., have a prior `invite` event from a mod) before this event. `join` events from uninvited users in private groups are stored by relays but discarded from client state.

### 5.3 `leave`

A user leaves the group. The author signs this event themselves.

Content schema:

```json
{}
```

Authorisation: always permitted by the `author` themselves.

### 5.4 `invite`

Invites a user to the group. Required before `join` in private groups.

Content schema:

```json
{
  "invitee": "<user pubkey hex>",
  "role":    "member"
}
```

| Field | Type | Description |
|---|---|---|
| `invitee` | string | Pubkey of the user being invited (64-char hex). |
| `role` | string | Role being offered. Currently only `"member"` is defined. Reserved for future extension. |

Authorisation: the `author` MUST be in the group's `mods` set at the point in the DAG immediately before this event.

### 5.5 `kick`

Removes a user from the `joined` set. The user may re-join (this does not ban).

Content schema:

```json
{
  "target": "<user pubkey hex>"
}
```

Authorisation: `author` MUST be a mod.

### 5.6 `ban`

Bans a user. A banned user is removed from `joined` and prevented from re-joining until `unban` is issued or the `until` timestamp passes.

Content schema:

```json
{
  "target": "<user pubkey hex>",
  "until":  null,
  "reason": "Spamming"
}
```

| Field | Type | Description |
|---|---|---|
| `target` | string | Pubkey of the user being banned. |
| `until` | integer or null | Optional Unix timestamp after which the ban expires. `null` means permanent. |
| `reason` | string | Free-text reason. May be empty. |

Authorisation: `author` MUST be a mod.

### 5.7 `unban`

Lifts a ban.

Content schema:

```json
{
  "target": "<user pubkey hex>"
}
```

Authorisation: `author` MUST be a mod.

### 5.8 `mod_add`

Promotes a member to moderator.

Content schema:

```json
{
  "target": "<user pubkey hex>"
}
```

Authorisation: `author` MUST be a mod.

### 5.9 `mod_remove`

Demotes a moderator to regular member.

Content schema:

```json
{
  "target": "<user pubkey hex>"
}
```

Authorisation: `author` MUST be a mod. A mod MAY demote themselves.

### 5.10 `relay_update`

Updates the canonical relay list.

Content schema:

```json
{
  "relays": ["wss://relay1.example.com", "wss://relay2.example.com"]
}
```

The `relays` array MUST be non-empty. All URLs MUST use the `wss://` scheme.

Authorisation: `author` MUST be a mod.

### 5.11 `metadata_update`

Updates group name or description.

Content schema:

```json
{
  "name":        "New Name",
  "description": "New description"
}
```

Both fields are optional. Only fields present in the content are updated; absent fields are left unchanged.

Authorisation: `author` MUST be a mod.

---

## 6. The `chat` Namespace (Default App)

These event types cover basic group chat features and are part of the protocol's default app namespace. They use the `chat.` prefix.

### 6.1 `chat.message`

A chat message from a joined member.

Content schema:

```json
{
  "text":      "Hello, world.",
  "channel":   "general",
  "reply_to":  "<event id hex>"
}
```

| Field | Type | Description |
|---|---|---|
| `text` | string | Message text. Non-empty. |
| `channel` | string | Channel name within the group. Non-empty. Names are case-sensitive. |
| `reply_to` | string or null | Optional event ID of the message being replied to. If present, MUST be a valid event ID (64-char hex). If absent or `null`, the message is not a reply. |

Authorisation: `author` MUST be in the `joined` set and not in the `banned` set at this point in the DAG.

### 6.2 `chat.reaction`

A reaction to a message.

Content schema:

```json
{
  "target": "<event id hex>",
  "emoji":  "+1"
}
```

| Field | Type | Description |
|---|---|---|
| `target` | string | Event ID of the message being reacted to. |
| `emoji` | string | Emoji or short text representing the reaction. Non-empty. |

Authorisation: `author` MUST be in the `joined` set and not in the `banned` set.

### 6.3 `chat.nickname_set`

Self-asserted nickname for the signing user.

Content schema:

```json
{
  "nickname": "Alice"
}
```

| Field | Type | Description |
|---|---|---|
| `nickname` | string | The nickname the user is asserting for themselves. Non-empty. |

Authorisation: `author` MUST be in the `joined` set. The nickname applies to the signing user only; the most recent `chat.nickname_set` event from a given user (in canonical linearisation order) determines their display name.

### 6.4 `chat.channel_create`

A mod creates a new channel in the group.

Content schema:

```json
{
  "name": "announcements"
}
```

| Field | Type | Description |
|---|---|---|
| `name` | string | Channel name. Non-empty, case-sensitive, max 50 chars. Alphanumeric, hyphens, and underscores permitted. |

Authorisation: `author` MUST be a mod. The `name` MUST NOT already exist in `channels`. The event is stored but discarded from state on duplicate names.

### 6.5 `chat.channel_delete`

A mod deletes an existing channel.

Content schema:

```json
{
  "name": "announcements"
}
```

Authorisation: `author` MUST be a mod. The `"general"` channel MUST NOT be deleted. Messages in a deleted channel remain in the DAG but are hidden from normal rendering (the channel name is removed from `channels`, so `chat.message` events with that channel name are filtered from display). Clients MUST NOT delete events from storage when a channel is deleted.

---

## 7. The Causal DAG

### 7.1 Structure

Events form a DAG where edges are parent references. The `parents` array of an event lists the event IDs of its causal parents.

Rules:
- The `genesis` event of a group MUST have an empty `parents` array.
- Every other event MUST have at least one parent. (Most events refer to the heads the author had at composition time; parents may include any events, not strictly the heads, but referring to heads is the recommended pattern.)
- The graph MUST be acyclic. An event's parents MUST NOT transitively include the event itself. Implementations MUST detect and reject cycles.
- Parent IDs MUST NOT be empty strings.

### 7.2 Heads

A **connected event** is an event in the same connected component as the group's `genesis` event: either the genesis event itself, or an event whose full parent set is already connected. Events with absent parents are **not connected** until every missing parent chain has been healed back to genesis.

A **head** is a connected event that no other connected event extends. When composing an event, the author SHOULD include all current connected heads as parents (and only those heads). This pattern maximises completeness propagation while preventing orphan or locally failed events from poisoning future parent selection.

Clients MUST NOT select an event as a parent unless that event is connected to genesis in the client's local store. Clients SHOULD NOT select locally-created events that have not been accepted by any canonical relay; such events remain retryable local drafts until delivery succeeds.

Other parent-selection strategies are valid only if every selected parent is connected to genesis. Selecting disconnected parents is non-conformant.

### 7.3 Gaps

A **gap** occurs when an event's `parents` contains an ID for which the client does not have the corresponding event.

Gap rules:
- Gaps are **visible**: the missing hash is known from the child's `parents`.
- Gaps are **addressable**: a client can request the missing event by its exact ID via `get` (Section 10.4.3).
- Gaps are **not fatal to storage**: events that depend on a gap may still be stored and used as evidence that a missing parent exists.
- Gappy/disconnected events are **not normal history**: until connected, they MUST NOT be applied to group state, rendered as ordinary messages, or selected as parents by subsequent events.
- Clients MUST NOT discard events solely because their parents are absent.

### 7.4 Gap Healing Procedure

When a client detects a missing parent ID:

1. Send a `get` request for that ID to every canonical relay the client is connected to.
2. The first relay that returns the event wins; the client verifies it (`verify_event`, Section 3.5) and stores it.
3. If only some relays have the event, the client SHOULD republish it to the relays that returned `not_found` (backfill — Section 9.4).
4. If no relay has the event, the client marks the gap as **unresolvable for now** and retries later (e.g., on next sync, or when another event references the same parent). The child event remains disconnected until the parent chain is healed.

---

## 8. Group State

### 8.1 State Model

Group state is derived entirely by replaying the genesis-connected subset of the DAG in a deterministic order. Any client with the same connected event history will derive identical state.

The state consists of:

```
members:    set of pubkey hex strings (invited users)
joined:     set of pubkey hex strings (currently joined users)
banned:     map of pubkey hex -> {until: int|null, reason: string}
mods:       set of pubkey hex strings
relays:     list of relay URL strings
metadata:   {name: string, description: string}
public:     boolean
app:        string (primary app namespace, e.g. \"chat\")
channels:   set of channel name strings (when app is \"chat\")
```

### 8.2 Initialisation

State is initialised from the `genesis` event:

- `members = {founder}`  (the founder is implicitly a member)
- `joined = {founder}`   (the founder is automatically joined)
- `banned = {}`
- `mods = genesis.content.mods`  (MUST include the founder)
- `relays = genesis.content.relays`
- `metadata = {name: genesis.content.name, description: genesis.content.description}`
- `public = genesis.content.public`
- `app = genesis.content.app`  (e.g., `"chat"`)
- `channels = set(genesis.content["chat.channels"])`  (when `app == "chat"`; MUST contain `"general"`)

### 8.3 Derivation Order

Connected events are applied in **(ts, id)** order: ascending `ts`, with ties broken by lexicographic comparison of the `id` field (ascending). This is the **canonical linearisation order**.

The authorisation check for an event is performed against the state *immediately before that event in canonical linearisation order* (i.e., the state resulting from applying all earlier events).

Disconnected events are stored but excluded from canonical linearisation for state derivation until they become connected to genesis through gap healing.

### 8.4 Derivation Rules

For each event in canonical linearisation order (skipping the genesis, which has already been used to initialise state):

| Event type | Effect on state (if authorised) |
|---|---|
| `invite` | Add `content.invitee` to `members`. |
| `join` | If `public == true` OR `author` is in `members`: add `author` to `joined`. If `author` is in `banned` and (banned entry's `until` is null or `until > event.ts`): discard (do not add to `joined`). |
| `leave` | Remove `author` from `joined`. |
| `kick` | Remove `content.target` from `joined` and `mods` (keep in `members`). |
| `ban` | Add `{until: content.until, reason: content.reason}` to `banned[target]`. Remove `content.target` from `joined`. |
| `unban` | Remove `content.target` from `banned`. |
| `mod_add` | Add `content.target` to `mods`. |
| `mod_remove` | Remove `content.target` from `mods`. |
| `relay_update` | Replace `relays` with `content.relays`. |
| `metadata_update` | For each field present in `content`, update `metadata[field]`. |
| `chat.channel_create` | Add `content.name` to `channels`. Reject if `content.name` is already in `channels`. |
| `chat.channel_delete` | Remove `content.name` from `channels`. Reject if `content.name` is `"general"`. |
| (any other type) | No state effect. The event is stored but does not affect group state. |

### 8.5 Authorisation

Before applying a state-change event, the implementation verifies:

- For `genesis`: already used for init; no further auth check.
- For `join` and `leave`: always permitted (the `author` signs for themselves).
- For `invite`, `kick`, `ban`, `unban`, `mod_add`, `mod_remove`, `relay_update`, `metadata_update`: the `author` MUST be in the current `mods` set (i.e., `mods` at the canonical linearisation point immediately before this event).
- For `chat.*` events: the `author` MUST be in `joined` and not in `banned` at this point.

Events failing authorisation are **discarded from state** but kept in the event store (see Section 10.3). Connected unauthorised events count toward canonical linearisation for the purposes of ordering other events, but they do not modify state. Disconnected events do not enter state linearisation until connected.

**Note on founder demotion:** The founder is the initial mod (set in `genesis.content.mods`), but has no special authority beyond that. A founder can demote themselves via `mod_remove` (transferring sole control to the remaining mods), and other mods can demote the founder. This is intentional: the group is governed by its current mod set, not by an irrevocable founder. Groups that wish to preserve founder authority can maintain an out-of-band social agreement, but the protocol does not enforce it.

### 8.6 Conflict Resolution

When two events have the same `ts` and affect the same state field, the event with the lexicographically greater `id` wins (i.e., is applied second).

Because the canonical linearisation order is `(ts, id)`, this is automatic: among same-`ts` events, the one with the larger `id` is applied after the one with the smaller `id`.

### 8.7 Ban Semantics

- A `ban` event adds the target to the `banned` map with an optional `until` timestamp and a `reason`.
- A banned user is removed from `joined` immediately.
- A `join` event from a banned user is discarded (the user cannot re-join while banned).
- A ban persists until either an `unban` event is issued OR the `until` timestamp passes. When checking whether a user is banned at time T (for evaluating a `join` or `chat.message` at time T), an entry in `banned[target]` is considered active iff `until == null` OR `until > T`. (Once expired, the user may `join` again without an `unban`.)
- A `kick` does NOT add to the `banned` map — the user remains in `members` and may re-join.
- A `mod_add` on a banned user does NOT lift the ban. The ban must be lifted via `unban` first (or expire via `until`).

### 8.8 Posting Authorisation

Only users in the `joined` set and not currently in the `banned` set may publish app-level events (e.g., `chat.message`, `chat.reaction`). Events from banned or non-joined users are stored by relays (relays do not enforce authorisation — see Section 10.3) but discarded by clients from state.

Users can view group history and state without joining. Joining is required only to post.

---

## 9. Completeness Layer

The completeness layer provides detection and self-healing for relay censorship. It is worth restating its limits: complete impossibility of censorship would require consensus (out of scope). The mechanisms here make censorship **detectable and provable** when attempted by 1-2 bad relays in a curated set.

### 9.1 Receipts

A **receipt** is a signed object returned by a relay to a publishing client after the relay has accepted and stored an event. It is **NOT** an event in the DAG.

#### 9.1.1 Receipt Format

```json
{
  "event_id": "<event id hex>",
  "group":    "<group pubkey hex>",
  "relay":    "<relay pubkey hex>",
  "ts":       1711234567,
  "sig":      "<ed25519 signature hex>"
}
```

| Field | Type | Description |
|---|---|---|
| `event_id` | string | The ID of the event being acknowledged. |
| `group` | string | The group pubkey (matches the event's `group` field). |
| `relay` | string | The relay's pubkey (signs this receipt). |
| `ts` | integer | Unix timestamp when the relay received the event. |
| `sig` | string | Ed25519 signature by the relay's key over the receipt's canonical serialisation. |

#### 9.1.2 Receipt Canonical Serialisation

```
[event_id, group, relay, ts]
```

A JSON array in this exact order, with the same serialisation rules as events (Section 3.3). No `id` or `sig` fields are included in the canonical form (the `sig` is computed over the canonical form).

```
sig = lowercase_hex( Ed25519Sign( relay_privkey, canonical_serialisation_bytes ) )
```

#### 9.1.3 Receipt Verification

A receipt is valid iff:
- The `sig` is a valid Ed25519 signature over the canonical serialisation using the `relay` field as the public key.
- The `event_id`, `group`, `relay` fields are valid 64-char hex strings.
- `ts` is a positive integer.

#### 9.1.4 Receipt Storage and Sharing

Receipts are **author-local**: the publishing client keeps them in local storage alongside the event. They are NOT events and are NOT routinely gossiped or propagated.

Receipts are shared **on-demand only**: when the author detects that a relay's attestation (or response to a `get`) contradicts a receipt they hold, the author publishes a fraud proof (Section 9.5) containing the receipt. This is the only circumstance in which receipts are shared.

### 9.2 Attestations

A relay periodically publishes a signed **attestation** committing to its known set of events for a group.

#### 9.2.1 Attestation Format

```json
{
  "group":    "<group pubkey hex>",
  "relay":    "<relay pubkey hex>",
  "set_hash": "<sha256 hex>",
  "tips":     ["<event id hex>", ...],
  "count":    1452,
  "prev":     "<attestation hash hex or null>",
  "ts":       1711234567,
  "sig":      "<ed25519 signature hex>"
}
```

| Field | Type | Description |
|---|---|---|
| `group` | string | Group pubkey. |
| `relay` | string | Relay pubkey (signs the attestation). |
| `set_hash` | string | SHA-256 of all known event IDs (sorted, see 9.2.3). |
| `tips` | array of strings | The DAG frontier: event IDs that have no children in this relay's store. Sorted lexicographically. |
| `count` | integer | Total number of events stored for this group. |
| `prev` | string or null | Hash of the relay's previous attestation for this group, or `null` if this is the first. Forms a per-relay attestation chain. |
| `ts` | integer | Unix timestamp when the attestation was issued. |
| `sig` | string | Ed25519 signature by the relay over the canonical serialisation. |

#### 9.2.2 Attestation Canonical Serialisation

```
[group, relay, set_hash, tips, count, prev, ts]
```

- A JSON array in this exact order, with the same serialisation rules as events (Section 3.3).
- `tips` is sorted lexicographically before serialisation.
- `prev` is `null` if this is the first attestation; serialised as the JSON literal `null`.
- The `prev` field of a subsequent attestation is `lowercase_hex( SHA256( canonical_serialisation_of_previous_attestation ) )`.

#### 9.2.3 set_hash Computation

Given all event IDs (64-char lowercase hex strings) known to the relay for this group:

1. Sort the IDs lexicographically (byte-wise comparison of UTF-8 bytes).
2. Concatenate them, separated by `\n` (newline, U+000A). The resulting string has one ID per line; trailing newline is NOT added.
3. UTF-8 encode the resulting string.
4. `set_hash = lowercase_hex( SHA256( bytes ) )`.

If the relay knows zero events for the group (only possible transiently before the first event), `set_hash` is the SHA-256 of the empty string:
```
set_hash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
```

#### 9.2.4 Attestation Verification

An attestation is valid iff:
- The `sig` is a valid Ed25519 signature over the canonical serialisation using the `relay` field as the public key.
- All hex fields (`group`, `relay`, `set_hash`, each element of `tips`, `prev` if non-null) are valid 64-char hex strings.
- `tips` is sorted lexicographically.
- `count >= 0` is an integer.
- `ts` is a positive integer.
- If `prev` is non-null and the client has the previous attestation, the `prev` field MUST match `SHA256(canonical_serialisation_of_previous_attestation)`.

#### 9.2.5 Issuance

A relay SHOULD issue a new attestation per group:
- Every N seconds (suggested: 5 seconds), OR
- Every N new events ingested (suggested: 100), whichever comes first.

Attestations are pushed to clients subscribed to the group without requiring a request (Section 10.5). Clients may also request the latest attestation on demand (Section 10.4).

### 9.3 Monitor Pass (Client Side)

There is no separate monitor role. Every client that reads a group is automatically a monitor by running the monitor pass whenever a new attestation is pushed by a relay.

#### 9.3.1 Inputs

The client maintains, per group:
- `known_set`: the set of event IDs the client has verified and stored locally.
- A reference to each relay's latest attestation.

#### 9.3.2 Algorithm

For each new attestation A pushed by relay R for group G:

1. **Verify the attestation** (Section 9.2.4). If invalid, record a fault for R in the local trust ledger and ignore.

2. **Compare `set_hash` to the client's `known_set`**:
   - Compute `set_hash_local = compute_set_hash(known_set)`.
   - If `set_hash_local == A.set_hash`: the relay is in sync with the client. Done.
   - If different, continue to step 3.

3. **Detect missing events**:
   - For each event ID in `known_set` that the client suspects might not be in R's set:
     - Query R for the event with `get` (Section 10.4). (This is an on-demand check; for efficiency, the client may pick a sample, but for correctness it must eventually check each missing event.)
     - If R returns `not_found`: this event is missing from R. Two sub-cases:
       - **Strong evidence (the client has a receipt)**: if the client is the author of the event and holds a receipt from R for this event, this is provable censorship. Assemble a fraud proof (Section 9.5).
       - **Weak evidence (no receipt)**: trigger backfill (Section 9.4). Fetch the event from a sibling relay and republish it to R. If R still does not include the event in its next attestation, record a fault.
   - For each event ID present in R's `tips` or referenced in R's responses that the client doesn't have: fetch from R and integrate (the client is the one lagging).

4. **Compare attestation to other relays'**:
   - For each other relay R' the client is connected to with its latest attestation A':
     - If `A.set_hash != A'.set_hash`: divergence detected. Investigate via step 3.
     - Because attestations are signed, if the client receives one attestation from R via client X's relay connection and another client Y reports a different attestation from R, the divergence is provable.

5. **Update local trust ledger**: record the latest attestation from R, plus any faults detected.

#### 9.3.3 Trust Ledger

The trust ledger is local state, not network consensus. It maps:

```
relay_pubkey -> {
  last_attestation: { ... },
  observed_faults: [ {ts, kind, event_id?, evidence}, ... ]
}
```

`kind` is one of: `invalid_attestation`, `missing_event_with_receipt`, `missing_event_no_receipt`, `attestation_chain_break`.

The protocol does not specify how clients act on faults. Different clients may have different policies (some de-list after one fault; others tolerate several). This is acceptable — the protocol guarantees misconduct is *provable*; the social response is up to clients and operators.

### 9.4 Backfill

When a client notices (via monitor pass or gap detection) that a relay R is missing event(s):

1. Fetch the missing event(s) from a sibling relay that has them (e.g., via `get`
   or `sync_ids`).
2. Verify each event (Section 3.5).
3. Acquire a sync lock on R (Section 10.4.10) to coordinate with other clients
   that may also be backfilling. If the lock is denied, short-lived clients MAY
   skip this relay for the current pass; long-lived clients SHOULD retry after
   the lease window by re-checking R's attestation first.
4. Republish the missing events to R via `backfill` (Section 10.4.12), NOT
   `publish`. The `backfill` action stores the event without broadcasting it to
   subscribers, since all subscribers either already have the event or will
   obtain it via their own sync.
5. Release the sync lock (Section 10.4.11).
6. R either:
   - Stores and integrates the events (its next attestation converges), or
   - Refuses to integrate (caught on the next monitor pass; recorded as a fault).

Backfill is performed by any client that notices a gap. The network drifts toward completeness over time without requiring dedicated monitor infrastructure.

### 9.5 Fraud Proofs

A **fraud proof** is a standalone object (NOT an event in the DAG) published when a client catches a relay misbehaving.

#### 9.5.1 Format

```json
{
  "type":      "fraud_proof",
  "group":     "<group pubkey hex>",
  "relay":     "<relay pubkey hex>",
  "event_id":  "<event id hex>",
  "event":     { ... full event object ... },
  "receipt":   { ... full receipt object ... },
  "evidence":  "<human-readable description of the contradiction>"
}
```

| Field | Type | Description |
|---|---|---|
| `type` | string | Always `"fraud_proof"`. |
| `group` | string | Group pubkey. |
| `relay` | string | The accused relay's pubkey. |
| `event_id` | string | The ID of the censored event. |
| `event` | object | The full event object (signed by its author, verifiable independently). |
| `receipt` | object | The receipt (signed by the relay, proving it received the event). |
| `evidence` | string | Description of the contradiction (e.g., "Relay's attestation at ts T omits event_id E, but relay's receipt at ts T' (T' < T) acknowledges receipt of E."). |

#### 9.5.2 Verification

A third party verifies a fraud proof by:

1. Verifying the `event` (Section 3.5): valid signature, valid hash.
2. Verifying the `receipt` (Section 9.1.3): valid signature by the `relay` pubkey over the receipt's canonical serialisation. The receipt's `event_id` MUST match the fraud proof's `event_id`.
3. Verifying the `evidence`: the contradiction must be checkable. For the typical case, the third party queries the relay (or its current attestation) and confirms the event is absent. (For attestation-based evidence, the third party must have a copy of the relay's later attestation that omits the event.)

#### 9.5.3 Distribution

Fraud proofs are distributed via the relay network using two WebSocket actions:

- **`submit_fraud_proof`** (Section 10.4.7): any client can submit a fraud proof to any relay. The relay validates it (Section 9.5.2) and, if valid, stores it in a side table (separate from the event store — fraud proofs are not events). The relay returns an ID derived from the fraud proof's canonical hash.
- **`query_fraud_proofs`** (Section 10.4.8): clients can query a relay for stored fraud proofs, optionally filtered by accused relay pubkey or by group. This lets clients discover censorship incidents they weren't present for.

Fraud proofs are NOT events in the DAG. They do not affect group state or the canonical linearisation. They are evidence about relay behaviour, stored and served by relays as audit data.

Clients SHOULD submit fraud proofs to multiple relays (not just the accused one) to maximise propagation. Clients SHOULD periodically query relays for new fraud proofs to keep their local trust ledger current.

Cross-client reputation effect is still local: each client verifies fraud proofs independently and decides how to act on them (e.g., de-list the accused relay from its trust ledger). There is no network-wide consensus on relay reputation.

#### 9.5.4 Fraud Proof Canonical Serialisation and ID

The canonical serialisation of a fraud proof is a JSON array with fields in this exact order, using the same serialisation rules as events (Section 3.3):

```
[type, group, relay, event_id, event, receipt, evidence]
```

Where `event` and `receipt` are the full canonical serialisations of those objects (recursively).

The fraud proof ID is:

```
id = lowercase_hex( SHA256( canonical_serialisation_bytes ) )
```

This ID is used by relays when acknowledging storage (Section 10.4.7 returns `{"type": "ok", "id": "<fraud proof id hex>"}`) and as a deduplication key (a relay that already has a fraud proof with the same ID SHOULD return the existing ID without re-storing).

The fraud proof is not signed by the submitter — it doesn't need to be. The proof's validity comes from the signatures within it (the event's author signature and the relay's receipt signature), both of which are independently verifiable. The submitter is merely a courier.

---

## 10. Relays

### 10.1 Role

Relays are storage and forwarding infrastructure. They have no authority over group state, membership, or identity. A relay cannot:
- Forge events (signatures defeat this)
- Rewrite events (modifying content breaks the signature)
- Silently censor (gaps, attestations, receipts all conspire to expose this)
- Control the group (authority is derived from the DAG, not from the relay)

A relay can only: choose not to host a group; lag behind (which is detectable and recoverable); fail entirely (shutdown — recoverable via other relays).

### 10.2 Canonical Relays

Each group has a set of **canonical relays** defined in current group state (via the genesis `relays` field and `relay_update` events). All new events are published to all canonical relays. All group history SHOULD be present on all canonical relays.

The invariant: at every point in time, all current canonical relays SHOULD hold identical complete history from genesis. Clients confirm this via attestation comparison.

Anyone can run a relay for an existing group at any time: fetch the log from existing relays, start serving. Only `relay_update` events (signed by a mod) make a relay canonical.

### 10.3 Relay Validation

When a relay receives an event via `publish` or `backfill` (from a client), it MUST:

1. Perform structural validation (Section 3.2). If invalid, reject without storing.
2. Check the maximum event size. Relays SHOULD reject events whose serialised size exceeds 64 KiB. (This protects against DoS via oversized events; see Section 16.5.)
3. Compute `id` and verify it matches. If not, reject.
4. Verify the signature (Section 3.5 step 3). If invalid, reject.
5. Verify the event belongs to a group the relay is hosting (i.e., the `group` field matches a known group). If not:
   - If the event is a valid `genesis` event (type is `genesis`, signature verifies against `group` field, all content fields present and valid), the relay SHOULD auto-host the group, subject to the relay operator's configured policy (Section 10.3.1). If auto-hosting is enabled and policy allows, the relay begins hosting the group and proceeds to step 6. Otherwise, reject.
   - For non-`genesis` events, reject.
6. Store the event.
7. Return a receipt to the publishing client. For `publish` actions, also
   broadcast the event to subscribed clients (Section 10.4.2). For `backfill`
   actions, do NOT broadcast (Section 10.4.12).

Relays MUST store events regardless of whether they hold the parent events. Relays MUST store events regardless of authorisation (i.e., a non-mod attempting a `kick` is stored, even though clients will reject it). Only structural and signature checks gate storage.

Relays SHOULD check whether an event is already stored before performing
expensive signature verification. If the event is already stored, the relay
returns a receipt without re-verifying, re-storing, or broadcasting. This is an
optimisation for backfill scenarios where many clients may republish the same
events to a recovering relay.

#### 10.3.1 Auto-Hosting Policy

Relays MAY auto-host new groups when they receive a valid `genesis` event, subject to operator-configured limits. Suggested policy options:

- **Open**: auto-host any valid genesis. Most permissive; relays become publicly usable infrastructure.
- **Allowlisted sources**: only auto-host genesis events whose `author` (founder) is in a configured allowlist. Curated but still operator-side.
- **Disabled**: never auto-host; only host groups the operator has explicitly configured. Most restrictive.

The default policy is at the relay operator's discretion. Regardless of policy, a relay that has begun hosting a group (whether via auto-host or explicit configuration) MUST continue to serve that group until the relay operator explicitly removes it or GC evicts its events (Section 10.7).

### 10.4 WebSocket Actions

Relays expose a JSON-over-WebSocket interface. Messages are JSON objects with an `action` field. Responses are JSON objects with a `type` field.

#### 10.4.1 Subscribe

Client → Relay:
```json
{"action": "subscribe", "group": "<group pubkey hex>"}
```

Relay → Client (ongoing):
```json
{"type": "event", "event": { ... }}
{"type": "attestation", "attestation": { ... }}
```

The client receives:
- All new events stored by the relay for that group (as `event` messages).
- All new attestations issued by the relay for that group (as `attestation` messages).

Subscriptions are persistent until the client unsubscribes or disconnects.

To unsubscribe, the client sends:
```json
{"action": "unsubscribe", "group": "<group pubkey hex>"}
```

or simply closes the WebSocket connection.

#### 10.4.2 Publish

Client → Relay:
```json
{"action": "publish", "event": { ... full event object ... }}
```

Relay → Client (response):
```json
{"type": "receipt", "receipt": { ... receipt object ... }}
```
or
```json
{"type": "error", "message": "<human-readable reason>"}
```

The relay performs validation (Section 10.3). If valid, stores the event, returns a signed receipt. If invalid, returns an error with a reason.

#### 10.4.3 Get (Specific Event)

Client → Relay:
```json
{"action": "get", "id": "<event id hex>"}
```

Relay → Client:
```json
{"type": "event", "event": { ... }}
```
or
```json
{"type": "not_found", "id": "<event id hex>"}
```

#### 10.4.4 Sync (Bulk Fetch)

Client → Relay:
```json
{"action": "sync", "group": "<group pubkey hex>", "since": 1711234567}
```

Relay → Client (multiple messages):
```json
{"type": "event", "event": { ... }}
{"type": "event", "event": { ... }}
...
{"type": "sync_complete", "group": "<group pubkey hex>", "count": 1452}
```

The relay sends all events it has stored for the group with `ts > since` (or all events, if `since` is omitted). Events are sent in arbitrary order. The client is responsible for canonical-linearising them locally.

`sync_complete` is sent when the relay has finished delivering events for this request.

#### 10.4.5 Request Attestation

Client → Relay:
```json
{"action": "attestation", "group": "<group pubkey hex>"}
```

Relay → Client:
```json
{"type": "attestation", "attestation": { ... }}
```

Returns the relay's latest signed attestation for the group. If the relay does not host the group, returns:
```json
{"type": "error", "message": "group not hosted"}
```

#### 10.4.6 Unsubscribe

Client → Relay:
```json
{"action": "unsubscribe", "group": "<group pubkey hex>"}
```

Relay → Client:
```json
{"type": "ok", "message": "unsubscribed"}
```

#### 10.4.7 Submit Fraud Proof

Client → Relay:
```json
{"action": "submit_fraud_proof", "fraud_proof": { ... full fraud proof object ... }}
```

Relay → Client:
```json
{"type": "ok", "id": "<fraud proof id hex>"}
```
or
```json
{"type": "error", "message": "<human-readable reason>"}
```

The relay validates the fraud proof (Section 9.5.2). If valid, it stores the fraud proof in a side table (not in the event store — fraud proofs are not events). The relay assigns the fraud proof an ID derived from the hash of its canonical serialisation (Section 9.5.4) and returns it to the submitter.

Relays SHOULD accept valid fraud proofs regardless of who submits them. Relays MAY rate-limit submissions to prevent abuse.

#### 10.4.8 Query Fraud Proofs

Client → Relay:
```json
{"action": "query_fraud_proofs", "relay": "<accused relay pubkey hex>"}
```

or
```json
{"action": "query_fraud_proofs", "group": "<group pubkey hex>"}
```

Relay → Client (multiple messages):
```json
{"type": "fraud_proof", "fraud_proof": { ... }}
{"type": "fraud_proof", "fraud_proof": { ... }}
...
{"type": "query_complete", "count": 3}
```

Returns all fraud proofs the relay has stored, optionally filtered by accused relay pubkey or by group. Clients use this to discover censorship incidents they weren't present for.

#### 10.4.9 Sync IDs (Bulk ID Fetch)

Client -> Relay:
```json
{"action": "sync_ids", "group": "<group pubkey hex>"}
```

Relay -> Client:
```json
{"type": "ids", "group": "<group pubkey hex>", "ids": ["<event id hex>", "..."]}
```

Returns all event IDs the relay has stored for the group, without full event
bodies. Clients use this to compute a set difference against their local known
set, then fetch only missing full events via `get` (Section 10.4.3) and
backfill only events the relay is missing via `backfill` (Section 10.4.12).

If the relay does not host the group, it returns:
```json
{"type": "error", "message": "group not hosted"}
```

#### 10.4.10 Sync Lock

A sync lock coordinates backfill so multiple clients discovering the same relay
divergence do not all backfill simultaneously. The lock is per-group,
lease-based, and advisory.

Client -> Relay:
```json
{"action": "sync_lock", "group": "<group pubkey hex>", "client_id": "<user pubkey hex>"}
```

Relay -> Client (granted):
```json
{"type": "sync_lock_granted", "group": "<group pubkey hex>", "ttl": 30}
```

Relay -> Client (denied):
```json
{"type": "sync_lock_denied", "group": "<group pubkey hex>", "expires_in": 15}
```

Rules:
- The lock is per-group.
- `client_id` is the client's user pubkey when available.
- TTL is 30 seconds. A holder SHOULD renew at roughly 60% of TTL during long
  backfills.
- If the same `client_id` requests the lock again, the lease is renewed.
- Expiry is lazy; relays do not set timers.
- The lock is advisory. Clients that do not support it fall back to
  uncoordinated backfill, which is safe due to relay-side deduplication.

#### 10.4.11 Sync Unlock

Client -> Relay:
```json
{"action": "sync_unlock", "group": "<group pubkey hex>", "client_id": "<user pubkey hex>"}
```

Relay -> Client:
```json
{"type": "ok", "message": "unlocked"}
```

Releases the sync lock for the group if `client_id` matches the current holder.
If the holder does not match or no lock exists, the relay still returns `ok`.

#### 10.4.12 Backfill

Client -> Relay:
```json
{"action": "backfill", "event": { "...": "full event object" }}
```

Relay -> Client:
```json
{"type": "receipt", "receipt": { "...": "receipt object" }}
```
or
```json
{"type": "error", "message": "<human-readable reason>"}
```

`backfill` is identical to `publish` except the relay does NOT broadcast the
event to subscribed clients. It is used for historical events during healing and
new relay seeding. `publish` remains the action for newly-created events.

### 10.5 Attestation Push

In addition to on-demand requests (Section 10.4.5), relays SHOULD push new attestations to all subscribed clients automatically when they issue them:

```json
{"type": "attestation", "attestation": { ... }}
```

Clients MUST handle receiving unsolicited attestation messages on a subscription and treat them as new attestations for the monitor pass.

### 10.6 Relay Metadata Endpoint

Relays expose their public key and metadata via an HTTPS GET endpoint at the relay's base URL. The base URL is derived from the relay's WebSocket URL by scheme substitution:

- `wss://relay.example.com/` → `https://relay.example.com/`
- `ws://relay.example.com/` → `http://relay.example.com/`

The path is preserved. For example, `wss://relay.example.com/fern` maps to `https://relay.example.com/fern`. Clients send a GET request to this URL and expect a JSON response.

Response fields:

```json
{
  "name":        "Example Relay",
  "description": "A FERN relay",
  "pubkey":      "<relay pubkey hex>",
  "software":    "fern-relay-python",
  "version":     "0.1.0",
  "groups":      ["<group pubkey hex>", ...],
  "retention":   {
    "default": "full"
  }
}
```

| Field | Type | Description |
|---|---|---|
| `name` | string | Human-readable relay name. |
| `description` | string | Optional description. |
| `pubkey` | string | The relay's Ed25519 public key (64-char hex). Used to verify receipts and attestations. |
| `software` | string | Software identifier (e.g., `fern-relay-python`). |
| `version` | string | Software version. |
| `groups` | array of strings | Group pubkeys the relay hosts. (May be omitted if the relay doesn't wish to disclose.) |
| `retention` | object | Retention policy advertisement. `default` is one of `"full"` (keep all history) or `"last-N-days"` or `"last-N-events"`. Detailed schema TBD. |

Clients MUST verify they are connecting to the relay over TLS (HTTPS/WSS) before trusting the published public key. The first time a client sees a relay's public key, it MUST store it pinned to the relay's URL. On subsequent connections, the client MUST verify the relay's TLS certificate and the published public key matches the pinned one. (This binds the TLS identity to the FERN identity.)

### 10.7 Relay Garbage Collection

Because relays store all events with valid signatures (including events that fail authorisation and no client will honour), storage can grow with spam. The GC rule addresses this:

#### 10.7.1 Rule

When a relay holds more than N events in a group, it MAY delete events that are:
- **Tips**: have no children (no event in the relay's store has this event's ID in its `parents` array).
- **Unreferenced for N events**: have not been used as a parent by any of the subsequent N events (i.e., the relay received N events after this one, none of which referenced it as a parent).

Both conditions MUST hold simultaneously. An event is only GC'd if both are true.

#### 10.7.2 Properties

- The relay does not need to understand event authorisation — it only tracks parent references.
- An event is GC'd only if the DAG grew by N events without it being referenced.
- Events that are legitimately old tips (e.g., the last message in a quiet group) are also eligible, but only after the threshold.
- Clients MUST NEVER GC from their local cache — this rule applies only to relays.
- The suggested default threshold is `N = 1000`. Relays MAY choose a different N per group.

#### 10.7.3 Recovery

GC'd events on one relay are still present on other canonical relays and in client local caches. If a relay GC's an event that is later needed (e.g., requested via `get` or referenced as a missing parent), the relay returns `not_found`, and the client fetches it from a sibling relay during backfill.

GC is a per-relay storage optimisation, NOT a history deletion operation.

### 10.8 Relay Storage Requirements

A canonical relay MUST:
- Store all events it receives (subject to GC).
- Issue attestations periodically (Section 9.2.5).
- Return receipts for `publish` actions.
- Return receipts for `backfill` actions without broadcasting.
- Serve all stored events via `get`, `sync`, and `sync_ids`.
- Push new events and attestations to subscribed clients.

A canonical relay MAY:
- Choose its own GC threshold.
- Choose its own attestation cadence (within reason).
- Decline to host a group (it should then not be a canonical relay).

A canonical relay SHOULD:
- Check `has_event` before expensive verification on `publish` and `backfill`.
- Support `sync_lock` and `sync_unlock` for coordinated backfill.

---

## 11. Public vs Private Groups

### 11.1 Public Groups

In a public group (`public: true` in genesis), any user may publish a `join` event freely without a prior `invite`. Relays accept and store events from any author. This is the primary use case — Discord-like public servers.

### 11.2 Private Groups

In a private group (`public: false` in genesis), a user MUST have a valid `invite` event from a mod before they can publish a `join` event. The group address MUST be shared privately (out-of-band). Clients MUST reject `join` events from users not in `members`.

Relays do not enforce the public/private distinction — they store any well-formed event. The distinction is enforced by clients during state derivation (Section 8.5).

---

## 12. Group Address and Discovery

### 12.1 Group Address Format

A group address is the canonical way to share a group reference:

```
fern:<group_pubkey>@<relay1>,<relay2>,<relay3>
```

Example:
```
fern:9e7b4a2f...c2d4e6f@wss://relay-a.example.com,wss://relay-b.example.com
```

The `fern:` scheme prefix is optional but recommended. The relay URLs are starting points only — clients derive the authoritative relay list from the signed group state (genesis `relays` field and `relay_update` events) after receiving any events.

### 12.2 Initial Discovery

1. Client receives a group address out-of-band (invite link, shared URL, etc.).
2. Client connects to the hint relays via WSS.
3. Client fetches each relay's metadata (Section 10.6) over HTTPS to obtain the relay pubkey (and pin it).
4. Client performs the genesis fetch procedure (Section 12.3) to obtain and verify the genesis event.

### 12.3 Genesis Fetch Procedure

To fetch the genesis event for an unknown group:

1. Connect to a hint relay and subscribe to the group:
   ```json
   {"action": "subscribe", "group": "<group pubkey>"}
   ```
2. Immediately request the latest attestation:
   ```json
   {"action": "attestation", "group": "<group pubkey>"}
   ```
3. The attestation's `tips` include event IDs. The client may request one of the tips via `get`. Through recursive `get` requests, walking the parents chain, the client will eventually arrive at the genesis event. Note: if any ancestor in the chain has been GC'd by this relay (Section 10.7), the walk will fail with `not_found`. In that case, the client should fall back to step 4.
4. Alternatively, the client may use `sync` (Section 10.4.4) to fetch all events for the group at once, then identify the genesis as the event with empty `parents`. This is simpler (no recursive walks) but transfers more data.

Relays MUST support `subscribe` + `sync` for an unknown group; if they do not host the group, they MUST return an error.

### 12.4 Migration Procedure

To migrate a group to a new relay set:

1. A mod publishes a `relay_update` event naming the new relay set.
2. All connected clients observe the update.
3. Each client MUST perform new relay seeding (Section 12.5) for any new relay in the set.
4. Clients begin publishing new events to the new relay set.
5. Old relays MUST NOT be decommissioned until every new relay holds the complete history. Clients confirm this by cross-referencing attestations across old and new relays.

The invariant: at every point in time, all current canonical relays SHOULD hold identical complete history from genesis. Migration is not considered complete until all new canonical relays have convergent attestations.

### 12.5 New Relay Seeding

When a client observes a `relay_update` adding a new relay to the canonical list:

1. Connect to the new relay, fetch its metadata, pin its pubkey.
2. Subscribe to the group on the new relay.
3. Request its latest attestation.
4. Compare its attestation to the client's local known-set.
5. Backfill any events the new relay is missing (via `backfill`) — these are not "new events", they are old events the relay hasn't seen yet.
6. Confirm the new relay's next attestation converges with the other relays' attestations.

Clients MUST perform this seeding before sending new messages to the group. The migration is not complete until all canonical relays' attestations converge.

---

## 13. Client Behaviour

### 13.1 Local Cache

Clients MUST persist their full local event history to disk. This cache is essential for gap healing and relay seeding. Clients MUST NOT evict events from the local cache unless explicitly instructed by the user.

The local cache is also where receipts are stored (author-local, per-section 9.1.4).

### 13.2 Joining a Group

1. Receive a group address out-of-band.
2. Connect to hint relays via WSS; fetch metadata; pin relay pubkeys.
3. Perform the genesis fetch procedure (Section 12.3) — verify the genesis signature against the `group` pubkey.
4. Derive the canonical relay list from genesis (and subsequent `relay_update` events as they are received). Connect to all canonical relays not already connected, fetch their metadata, pin their pubkeys.
5. Sync from all canonical relays. Use attestation comparison as a sync gate:
   request each relay's attestation, compare its `set_hash` to the local known
   set, and use `sync_ids` for efficient ID-only comparison when hashes differ.
   Relays that do not support the newer actions fall back to `sync`.
6. Verify all events (Section 3.5).
7. Request the latest attestation from each canonical relay using the `attestation` action (Section 10.4.5).
8. Verify completeness via attestation comparison across all canonical relays. If attestations diverge, investigate via monitor pass (Section 9.3) and backfill (Section 9.4), using sync locks to coordinate where supported.
9. Compute the genesis-connected event set. Store disconnected events as pending/gappy events and start gap healing for their missing parents.
10. Walk only the connected DAG in canonical linearisation order, applying state events to compute current group state.
11. Open live subscriptions on all canonical relays. This also begins receiving attestation pushes.
12. Begin running the monitor pass in the background on every attestation push.

### 13.3 Publishing an Event

1. Construct the event:
   - Set `type`, `group`, `author`, `content`, `ts`, `tags` (empty unless using extensions).
   - Set `parents` to the current connected heads (Section 7.2).
2. Compute the canonical serialisation, then `id` and `sig`.
3. Send `publish` to all canonical relays in parallel.
4. Collect receipts. The event is considered "safely acknowledged" once receipts from at least 2 canonical relays are received (configurable threshold).
5. Store receipts in local cache, indexed by `(event_id, relay_pubkey)`.
6. Cache the event locally.

Publishing to fewer than all canonical relays is strongly discouraged. Message loss resulting from single-relay publishing is the sender's responsibility.

If a locally-created event is cached before relay delivery succeeds, clients MUST NOT use it as a parent for later events until it is no longer considered local-only/failed. This prevents one failed send from creating a chain of permanently disconnected descendants.

### 13.4 Receiving (Live)

Clients maintain persistent WebSocket connections to all canonical relays. Messages from whichever relay delivers an event first are accepted. Duplicates (same event ID) are deduplicated.

On receiving a new event:
1. Verify (Section 3.5).
2. Store in local cache.
3. Recompute or incrementally update the genesis-connected event set.
4. If the event is disconnected, keep it pending and trigger gap healing. It MUST NOT be applied to state, rendered as an ordinary message, or used as a parent.
5. If the event is connected, update connected DAG heads.
6. If connected and state-changing, recompute/apply group state. Update `joined`, `banned`, etc. as needed.
7. Render connected events per client policy (hide banned/unauthorised if configured; show gaps prominently).

On receiving a new attestation:
1. Verify the attestation (Section 9.2.4).
2. Compare `set_hash` to the local known set.
3. If hashes match, the relay is in sync.
4. If hashes differ, run the monitor pass (Section 9.3) and trigger backfill
   (Section 9.4). Short-lived clients MAY skip relays whose sync lock is held;
   long-lived clients SHOULD retry after the lock lease using future
   attestation/sync triggers.
5. Update local trust ledger.

### 13.5 Displaying Gaps

When rendering group history, clients MUST visibly indicate known gaps — event IDs referenced as parents that are not present in the local cache. Gaps MUST NOT be silently hidden.

Disconnected events SHOULD be surfaced as pending/gap diagnostics rather than ordinary chat messages. Once the missing parent chain is healed and the event becomes connected to genesis, the event can enter normal rendering and state derivation.

This is the user-facing signal that censorship or network issues may be occurring.

### 13.6 Posting Authorisation (Client Side)

Before constructing and publishing an app-level event (e.g., `chat.message`), the client MUST verify that the author is currently in `joined` and not in `banned`. If not, the client SHOULD prevent the user from posting (but the protocol does not enforce this — relays will store whatever the client sends).

### 13.7 Trust Ledger

Each client maintains a local trust ledger (Section 9.3.3). Trust propagation is social, not protocol-enforced: the protocol does not dictate that faulted relays are de-listed across clients. Different clients may have different views of which relays are trustworthy. The protocol guarantees misconduct is *provable*; the social response is up to clients and operators.

---

## 14. Conformance

### 14.1 Conformance Classes

This specification defines three conformance classes:

- **FERN Client**: implements event creation, signing, verification, DAG operations, group state derivation, the monitor pass, backfill, and the WebSocket client actions (`subscribe`, `publish`, `backfill`, `get`, `sync`, `sync_ids`, `sync_lock`, `sync_unlock`, `attestation`).
- **FERN Relay**: implements event validation, storage, GC, attestation issuance, receipt issuance, and the WebSocket server actions, plus the metadata HTTPS endpoint.
- **FERN Chat App**: a FERN Client that additionally implements the `chat.*` event types (Section 6), rendering, and user interaction (CLI/GUI/Web).

### 14.2 Must / Should / May

The terms MUST, SHOULD, MAY are used per RFC 2119 throughout this specification.

---

## 15. Reference Algorithms

### 15.1 compute_set_hash

```
function compute_set_hash(event_ids: Set[str]) -> str:
    sorted_ids = sorted(event_ids)  # lexicographic byte-wise
    if len(sorted_ids) == 0:
        return sha256_hex(b"")
    joined = "\n".join(sorted_ids)
    return sha256_hex(joined.encode("utf-8"))
```

### 15.2 compute_connected_event_ids

```
function compute_connected_event_ids(events: List[Event]) -> Set[str]:
    by_id = {event.id: event for event in events}
    connected = Set()
    pending = Set(by_id.keys())

    changed = true
    while changed:
        changed = false
        for event_id in copy(pending):
            event = by_id[event_id]

            if event.type == "genesis" and event.parents == []:
                connected.add(event_id)
                pending.remove(event_id)
                changed = true
                continue

            if event.type != "genesis" and all(parent in connected for parent in event.parents):
                connected.add(event_id)
                pending.remove(event_id)
                changed = true

    return connected
```

### 15.3 compute_connected_heads

```
function compute_connected_heads(events: List[Event], excluded_ids: Set[str]) -> Set[str]:
    connected = compute_connected_event_ids(events) - excluded_ids
    referenced = Set()

    for event in events:
        if event.id not in connected:
            continue
        for parent in event.parents:
            if parent in connected:
                referenced.add(parent)

    return connected - referenced
```

`excluded_ids` is used for local-only or failed publish attempts. A client may keep those events in its local cache for retry, but MUST NOT choose them as parents until delivery succeeds.

### 15.4 verify_event

```
function verify_event(event: dict) -> Result:
    # 1. Structural validation
    if not has_required_fields(event): return Err("malformed")
    if not valid_hex(event["id"], 64): return Err("malformed")
    # ... all field validation per Section 3.2 ...
    if event["type"] == "genesis" and event["parents"] != []: return Err("malformed")
    if event["type"] != "genesis" and len(event["parents"]) == 0: return Err("malformed")
    
    # 2. Hash check
    canon = canonical_serialisation(event)
    if sha256_hex(canon.encode("utf-8")) != event["id"]: return Err("invalid_hash")
    
    # 3. Signature check
    if event["type"] == "genesis":
        pubkey = event["group"]
    else:
        pubkey = event["author"]
    if not ed25519_verify(pubkey, canon.encode("utf-8"), event["sig"]):
        return Err("invalid_signature")
    
    # 4. Authorisation (state-dependent; caller passes context)
    # (Done by caller — see Section 8.5)
    
    return Ok(event)
```

### 15.5 derive_group_state

```
function derive_group_state(events: List[Event]) -> State:
    connected_ids = compute_connected_event_ids(events)
    connected_events = [event for event in events if event.id in connected_ids]

    # Initialise from genesis
    genesis = find_genesis(connected_events)  # the unique event with type=="genesis"
    state = initialise_from_genesis(genesis)
    
    # Canonical linearisation order over connected events only
    sorted_events = sorted(
        [event for event in connected_events if event.id != genesis.id],
        key=lambda e: (e.ts, e.id),
    )
    
    for event in sorted_events:
        # Check authorisation against current state
        if not authorised(state, event):
            continue  # Skip — event is stored but not applied
        
        # Apply state change
        apply_state_change(state, event)
    
    return state
```

Disconnected events stay in storage for gap healing, but they are excluded from state derivation until their complete parent chain is connected to genesis.

### 15.6 monitor_pass

```
function monitor_pass(client, relay, new_attestation):
    # 1. Verify attestation
    if not verify_attestation(new_attestation):
        client.trust_ledger[relay.pubkey].add_fault("invalid_attestation")
        return
    
    # 2. Compare set_hash to known-set
    local_hash = compute_set_hash(client.known_set)
    
    if local_hash == new_attestation.set_hash:
        return  # In sync
    
    # 3. Detect missing events
    # (For efficiency, the client may sample; for correctness, must check each.)
    for event_id in client.known_set:
        response = relay.get(event_id)
        if response.is_not_found():
            receipt = client.receipts.get((event_id, relay.pubkey))
            if receipt is not None:
                # Provable censorship — fetch event from local cache to include in proof
                event = client.local_store.get(event_id)
                fraud_proof = build_fraud_proof(relay, event_id, event, receipt, "missing despite receipt")
                client.submit_fraud_proof(fraud_proof)
                client.trust_ledger[relay.pubkey].add_fault("missing_event_with_receipt", event_id)
            else:
                # Trigger backfill
                event = sibling_relays.fetch_event(event_id)
                if event is not None:
                    relay.backfill(event)
                client.trust_ledger[relay.pubkey].add_fault("missing_event_no_receipt", event_id)
    
    # 4. Compare to other relays' attestations
    for other_relay in client.connected_relays:
        if other_relay == relay: continue
        other_attestation = client.latest_attestations[other_relay.pubkey]
        if other_attestation and other_attestation.set_hash != new_attestation.set_hash:
            # Divergence — investigate via step 3
            pass
    
    # 5. Update local trust ledger
    client.trust_ledger[relay.pubkey].last_attestation = new_attestation
```

---

## 16. Security Considerations

### 16.1 Threat Model

The protocol's completeness guarantees are scoped to the **1-2 bad relays** scenario: a curated set of K≥3 trusted relays where fewer than K of them misbehave. All-relays-collude censorship is acknowledged as undetectable.

A relay that goes bad is in a trap: to avoid immediate detection it must accept events (so other relays don't visibly have events it doesn't), but once it accepts (and signs a receipt), subsequent omission is provable via receipt + attestation divergence. If it refuses to accept at all, it's immediately divergent from honest relays that do have the event.

### 16.2 Split-View Attacks

A relay could serve divergent attestations to different clients. Without signed attestations, this would be undetectable (a relay could deny serving a particular attestation to a particular client). With signed attestations (Section 9.2), two clients comparing the attestations they received from the same relay can prove the relay is lying to at least one of them.

This is defeated probabilistically by clients connecting from varied vantage points and comparing notes. Not perfect; same trust model as Certificate Transparency for HTTPS.

### 16.3 Group Key Compromise

The group private key is used only to sign the genesis event. If compromised, an attacker could publish a fraudulent genesis. Mitigations:
- The group key SHOULD be stored offline.
- It SHOULD be used only once and then destroyed.

**Note on future `group_rekey`:** A future protocol version may add a `group_rekey` event (signed by the current group key) to rotate to a new group key. This would weaken the "destroy after use" recommendation — if a group anticipates using `group_rekey`, it must retain the group private key, which increases the risk surface. Groups that do not anticipate rekeying SHOULD destroy the key after genesis; groups that do should retain it with strong protection (offline storage, hardware-backed key, threshold signing). The tradeoff is between forward security (destroy) and operational flexibility (retain).

### 16.4 Replay Attacks

Events carry a `ts` (timestamp) field. A replayed event would have an old `ts`. The protocol does not strictly enforce `ts` ordering relative to wall-clock time (clients tolerate past-dated events to handle clock skew). The canonical linearisation order (Section 8.3) ensures a deterministic application order regardless of network arrival order.

A malicious replay of a captured event by a relay (e.g., re-publishing an old event) is harmless — the event is already in clients' caches and is deduplicated by event ID.

### 16.5 DoS Vectors

- **Event flooding**: a malicious user could publish many events to overwhelm relays. Mitigation: relay-side admission policies (PoW, rate limits, IP limits) — out of scope of this protocol version; left to relay operators.
- **Receipt flooding**: a relay could be forced to issue many receipts (and thus be liable for many censorship proofs). Same mitigation — relay-side admission.
- **Large content**: a malicious user could publish events with enormous `content`. Relays SHOULD enforce a maximum event size (suggested: 64 KiB) and reject larger events.

### 16.6 IP Exposure

Clients connect only to relays; they never connect to each other. Relay operators can see client IPs. Other group members cannot. (Clients may further protect their IPs by using Tor or VPN at their option; the protocol does not mandate or specify this.)

### 16.7 Founder Misbehaviour

The founder's worst-case abuse is "issues bad bans" — bad admin decisions. Because bans are render-time filters (not log deletions), the underlying log stays complete and verifiable. Founder trust is an accepted assumption at group join time, same as joining any Discord server.

---

## 17. Future Extensions

These items are explicitly deferred. Each is documented as an upgrade path.

- **Threshold founder signing**: multiple admin keys required for high-sensitivity actions.
- **Merkle exclusion proofs**: third-party verification of non-inclusion without holding the full set.
- **Fork-proofs as first-class gossiped objects**: network-wide reputation propagation for relay misbehavior.
- **Snapshots**: founder-signed state anchors to bound new-joiner cost for large groups.
- **App-prefixed types with pubkey namespaces**: `<app_pubkey>.appname.type` for collision-free type names.
- **Protocol versioning**: a `protocol` field in genesis content for forward compatibility.
- **Relay-side policy enforcement**: relays checking group-state policies on ingest.
- **Extended app surfaces**: pins, channel policies, message edits. Apps can define these in their own event types.
- **`group_rekey` event**: rotation of the group private key (signed by the current group key).

---

## 18. Summary

FERN is a decentralised, censorship-resistant protocol for public group chats. The protocol defines:
- Cryptographic primitives (Ed25519, SHA-256)
- Event structure with canonical serialisation, signing, and verification
- A general-purpose type namespacing system with a default `chat` namespace
- The causal DAG for completeness propagation
- A deterministic group state machine folded from the genesis-connected DAG subset
- A completeness layer (receipts, attestations, monitor pass, backfill, fraud proofs)
- A relay protocol over WebSockets
- Discovery, migration, and client behaviour

The protocol is suitable for Discord-like public group chats that can't be forged, can't be silently censored (provably if attempted by 1-2 bad relays in a curated set), can't be shut down, and hide member IPs by virtue of never connecting members directly.
