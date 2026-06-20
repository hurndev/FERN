# Protocol-App Boundary Blueprint

This document defines the intended boundary between the FERN protocol layer and
application layers. It answers the open questions in `protocol-app-boundary.md`
and should be read as the target design, even if parts of the current
implementation still use older names or structures.

FERN is intended to support many kinds of communication applications: simple
group chats, Discord-like servers, forums, collaborative boards, polling spaces,
and future apps that share the same replicated signed-event substrate. The
protocol must therefore provide common group infrastructure without hard-coding
one app's product model.

The guiding rule is:

> The protocol defines durable group existence, authority roots, replication,
> membership, and auditability. Apps define communication objects, app-specific
> permissions, and user experience.

---

## 1. Layer Responsibilities

### 1.1 Protocol Layer

The protocol layer owns the things every FERN group needs regardless of app:

- cryptographic identity for users, groups, relays, and events
- canonical serialisation, hashing, signing, and verification
- the causal DAG and connectedness rules
- relay replication, receipts, attestations, backfill, and fraud proofs
- group discovery and relay migration
- group-level membership and participation state
- root group authority
- generic group metadata
- declaration of the group's primary app profile and app config keys

Protocol events are bare event types with no dot, such as `genesis`, `join`,
`leave`, `ban`, and `relay_update`.

### 1.2 App Layer

App layers own the product model built on top of the group:

- messages, posts, threads, pages, polls, events, boards, or other content
- channels, forum categories, topic areas, thread state, and app-specific
  structure
- reactions, edits, pins, locks, hides, and rendering policies
- app-specific settings
- app-specific roles and permissions
- display profiles, if the app wants more than raw pubkeys

App events use dotted event types, such as `chat.message`,
`forum.thread_create`, or `poll.vote`.

### 1.3 The Test

When deciding where a feature belongs, ask:

- Would every FERN app need this concept to maintain group existence,
  replication, or global participation? If yes, it belongs in the protocol.
- Is this about how a particular app structures or renders communication? If
  yes, it belongs in the app namespace.
- Does this grant power over relays, protocol metadata, or root authority? If
  yes, it must be protocol-level.
- Does this grant power only over app objects, such as channels, forum
  categories, or posts? If yes, it should be app-level.

---

## 2. Group Admins, Not Universal Moderators

FERN should use a protocol-level root authority concept, called **group admins**
in this document.

Group admins are not meant to be a complete moderation model for every app.
They are the root authority for protocol-owned group state.

Group admins can perform protocol actions such as:

- updating the canonical relay list
- updating generic group metadata
- changing the group admin set
- issuing global group participation actions, such as invites, kicks, and bans
- updating protocol-owned discovery fields

Apps may treat group admins as app superusers, but they are not required to use
group admins as their only permission model.

### 2.1 Why Not Protocol `mods`

The word "mod" implies app-level moderation. That becomes misleading for rich
apps. "Admin" is used here for root group authority instead.

A Discord-like app may need roles, channel-specific permissions, muted users,
thread moderators, and server administrators. A forum may need global
moderators, category moderators, trusted posters, locked-thread permissions, and
post-hiding privileges.

If the protocol exposes only "admins", apps will either:

- overload protocol admins for everything, making rich moderation impossible, or
- add app-level roles alongside protocol admins, creating two overlapping
  moderation concepts.

Using protocol **admins** avoids this confusion. Admins are root group authority.
App moderation is layered underneath.

### 2.2 App Permission Models

Apps have three valid choices:

1. Use group admins directly.
2. Use group admins as app superusers and define additional app-level roles.
3. Define a mostly independent app-level role model while still respecting
   protocol admins as the root authority.

Simple apps should choose option 1. Rich apps should choose option 2 or 3.

For example, the default `chat` app can say:

- joined, non-banned users may send messages
- group admins may create channels
- group admins may update chat settings

A Discord-like app can add:

- `chat.role_create`
- `chat.role_assign`
- `chat.channel_permission_update`
- `chat.mute`

A forum app can add:

- `forum.role_create`
- `forum.category_moderator_add`
- `forum.thread_lock`
- `forum.post_hide`

App-level roles must not grant protocol powers unless the protocol explicitly
defines a way to delegate those powers. An app role may let someone lock a
thread, but it cannot let them update the relay list.

---

## 3. Global Participation vs App Moderation

FERN should keep global participation controls in the protocol.

Protocol-level participation events apply to the group as a whole:

- `join`
- `leave`
- `invite`
- `kick`
- `ban`
- `unban`

These events answer whether a user can participate in the group at all. They are
not a complete moderation system for every app.

Apps may define scoped moderation on top:

- a chat app may mute a user in one channel
- a forum app may prevent a user from posting in one category
- a wiki app may remove edit access while preserving read access
- a Discord-like app may give users role-specific powers

The distinction is:

- protocol ban: "this pubkey is globally barred from group participation"
- app mute/category ban: "this pubkey is restricted within this app surface"

Relays still do not enforce either layer's authorisation rules. They store
well-formed, validly signed events. Clients derive state and decide which events
are authorised.

---

## 4. Generic Group Metadata

`name` and `description` are protocol-level group metadata.

They are display fields, but they are also discovery and trust fields. Every
communication app needs a human-readable way to identify a group. Duplicating
these as `chat.name`, `forum.name`, `board.name`, and so on would reduce
interoperability for no useful gain.

The protocol should therefore keep a generic metadata update event for fields
like:

- `name`
- `description`

Apps may define their own additional metadata in their namespaces:

- `chat.topic`
- `forum.guidelines`
- `wiki.home_page`

Generic clients should be able to show a group name and description even when
they do not understand the group's primary app.

---

## 5. Genesis Content

Genesis should contain both protocol config and initial app config.

Bare keys are protocol-owned. Dotted keys are namespace-owned.

Example:

```json
{
  "name": "My Server",
  "description": "A place to talk",
  "public": true,
  "founder": "<founder user pubkey hex>",
  "admins": ["<founder user pubkey hex>"],
  "relays": ["wss://relay.example.com"],
  "app": "chat",
  "chat.channels": [
    {"id": "general", "name": "general", "description": "", "position": 0}
  ],
  "chat.default_channel": "general",
  "chat.system_channel": "general"
}
```

This keeps genesis flat and easy to inspect while still giving apps a place to
bootstrap required initial state.

### 5.1 Bare Keys

Bare keys in genesis are reserved by the FERN protocol. Apps must not define
bare genesis keys.

Examples:

- `name`
- `description`
- `public`
- `founder`
- `admins`
- `relays`
- `app`
- future protocol fields such as `features` or `protocol`

### 5.2 Dotted Keys

Dotted keys in genesis are owned by app or extension namespaces.

Examples:

- `chat.channels`
- `chat.default_channel`
- `forum.categories`
- `forum.default_category`
- `poll.default_visibility`

Clients that do not understand a dotted genesis key must preserve it when
storing or forwarding events and ignore it when deriving app state.

The app that owns the namespace validates the schema for its own keys.

### 5.3 Why Flat Dotted Keys Instead of Nested `app_config`

Flat dotted keys match FERN's event type namespacing model:

- bare names are protocol-reserved
- dotted names are app or extension-owned

They are also easy to inspect in logs, simple to canonicalise, and avoid adding
a second nested configuration mechanism.

A nested `app_config` object is valid as a possible future design, but it is not
necessary. Flat dotted keys are sufficient if the reserved-key rule is clear.

---

## 6. The `app` Field

The `app` field declares the group's primary application profile.

It does not mean that only events from that namespace are valid protocol events.
Relays continue to store all well-formed, validly signed events regardless of
namespace. Clients render and derive app state only for namespaces they
understand.

The primary app tells clients what experience the group is meant to provide:

- `app: "chat"` means the group is primarily a chat space
- `app: "forum"` means the group is primarily a forum
- `app: "wiki"` means the group is primarily a wiki-like space

A client that does not understand the primary app should not pretend to offer
the full experience. It may still show generic group metadata and protocol
state.

### 6.1 Future Feature Declarations

FERN may later add a protocol-level `features` or `extensions` field:

```json
{
  "app": "forum",
  "features": ["poll", "chat"]
}
```

This would advertise optional namespaces the group expects clients to understand.
It should remain advisory for rendering and UX, not a relay storage rule.

Until such a field exists, clients can treat dotted event namespaces as
self-describing and ignore unknown ones.

---

## 7. Single App, Multiple Features

FERN groups should have one primary app profile, but they should not be limited
to one event namespace forever.

This avoids two bad extremes:

- every group is forced to pretend to be `chat`
- every group becomes an unstructured bag of unrelated event types

The intended model is:

- one primary app defines the main experience
- optional features or extensions can add additional dotted namespaces
- apps may reference events from other namespaces where their schemas allow it

For example:

- a chat group may include polls
- a forum may include reactions and polls
- a Discord-like app may include channels, roles, scheduled events, and polls

Polls do not need to live under `chat.poll` unless they are specifically a chat
sub-feature. A poll-centered or forum-centered group should be able to use a
`poll.*` namespace without pretending to be chat.

---

## 8. Object Events vs Settings Bags

Apps should use both granular object events and settings-bag events, with a
clear split.

Use object events for things with identity or lifecycle:

- channel create/update/delete
- category create/update/delete
- thread create/lock/archive
- poll create/close
- role create/assign/delete

Use settings bags for scalar or small configuration values:

- default channel
- system channel
- default category
- slow-mode defaults
- default poll visibility

Avoid a single broad `app.update` event that can mutate everything. It is harder
to audit, creates awkward conflict semantics, and makes the event log less
human-readable.

### 8.1 Channels

Channels are app-level objects, not protocol objects.

A chat app should prefer:

- `chat.channel_create`
- `chat.channel_update`
- `chat.channel_delete`

over replacing the full channel list through one settings field.

Object events handle concurrency better. If two admins create two channels at
the same time, both events can be applied. With a full replacement list, one
update can accidentally erase the other.

Channels should use stable IDs with mutable names. The reserved genesis channel
ID is `"general"`. Channels created after genesis use the ID of their
`chat.channel_create` event as the channel ID.

### 8.2 Settings

Settings such as `system_channel` should be app-level settings, not protocol
fields.

For example:

```json
{
  "type": "chat.settings_update",
  "content": {
    "system_channel": "audit",
    "default_channel": "general"
  }
}
```

The app namespace defines:

- which keys are allowed
- which users are authorised to update them
- how conflicts are resolved

For simple chat, group admins can update these settings. Rich apps may use
app-level roles.

---

## 9. App-Level Roles

Apps that need richer authority should define their own role or capability
events.

This is expected, not a failure of the protocol boundary.

Protocol admins are the root authority. App roles are application-specific
delegations beneath that root.

Examples:

```text
chat.role_create
chat.role_assign
chat.role_remove
chat.channel_permission_update

forum.role_create
forum.role_assign
forum.category_permission_update
forum.thread_moderator_add
```

Apps should specify how protocol admins interact with app roles. The recommended
default is:

- protocol admins are app superusers
- protocol admins can create or repair the app role graph
- app roles can grant app powers only
- app roles cannot grant protocol powers

This lets simple apps avoid a role system while allowing rich apps to grow one.

---

## 10. Example App Boundaries

### 10.1 Simple Chat

Protocol:

- group name and description
- admins
- joins/leaves/invites/bans
- relays

Chat app:

- messages
- reactions
- nicknames
- channels
- chat settings

Likely authorisation:

- joined, non-banned users can message
- joined users can set their own nickname
- group admins can create channels and update chat settings

### 10.2 Discord-Like App

Protocol:

- group name and description
- admins as root authority
- global participation controls
- relays

Discord-like app:

- channels and categories
- roles
- role assignment
- channel permissions
- mutes and scoped bans
- scheduled events
- polls
- pins
- message moderation

Likely authorisation:

- group admins are app superusers
- app roles handle day-to-day moderation
- scoped restrictions are app-level
- global bans remain protocol-level

### 10.3 Forum

Protocol:

- group name and description
- admins as root authority
- global participation controls
- relays

Forum app:

- categories
- threads
- posts
- post edits
- thread locks
- category moderators
- post hiding
- forum roles

Likely authorisation:

- joined, non-banned users can post where allowed
- forum roles control category and thread actions
- group admins can repair or override forum role state

---

## 11. Compatibility Rules

To preserve interoperability:

- relays store all well-formed, validly signed events regardless of namespace
- clients ignore app event types they do not understand
- clients preserve unknown dotted genesis keys
- bare event types and bare genesis keys are protocol-reserved
- dotted event types and dotted genesis keys are namespace-owned
- app roles cannot mutate protocol state
- protocol admins may be treated as app superusers
- group metadata remains generically readable by all clients

This allows specialised clients to offer rich experiences while generic clients
can still inspect group identity, membership, relay state, and audit evidence.

---

## 12. Summary

FERN should not be "the chat protocol with optional extras", and it should not
be an unstructured generic event bucket. It should be a group communication
substrate.

The protocol owns the common substrate: identity, replication, root authority,
global membership, metadata, and auditability. Apps own the communication model:
messages, posts, channels, threads, roles, settings, and rendering.

Protocol admins are root group authority, not a universal moderation system.
Simple apps can use them directly. Rich apps can build roles and capabilities
underneath them.

Genesis carries protocol config in bare keys and initial app config in dotted
namespace keys. The `app` field declares the primary app profile, while dotted
event namespaces remain extensible.

This boundary keeps FERN small enough to implement, universal enough for many
communication apps, and structured enough that independent clients can still
interoperate.
