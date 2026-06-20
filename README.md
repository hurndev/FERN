# FERN

FERN (Fault-tolerant Event Relay Network) is a signed-event protocol for decentralised public group chats. Users and groups are not tied to any particular server, and messages are published to multiple relay servers so the group survives relay outages and migrations.

Relay servers are intentionally simple. They store and forward signed events, return receipts when they accept them, and publish attestations about what they know. They do not make moderation decisions or invent group state. All verification happens locally in the client.

Message history is structured as a DAG (Directed Acyclic Graph). Events reference prior events, which makes history tamper-evident and lets clients heal missing history by redistributing events that relays are missing. Events with missing parents are stored, but they do not become normal history until the parent chain is complete.

FERN is inspired by [Nostr](https://nostr.com/), but it is designed specifically for group messaging: groups have a canonical relay set, history is verified for completeness across relays, and the network self-heals when relays diverge.

The protocol specification is here:

- [spec.md](spec.md)

The main design docs are here:

- [architecture.md](architecture.md)
- [python-architecture.md](python-architecture.md)
- [implementation-notes.md](implementation-notes.md)

## Current State

This repository contains:

- a Python reference implementation of the protocol
- a relay server and command-line tools
- Bracken, a browser-based chat client
- tests and supporting docs

The codebase is actively being worked on and is intended for local development and experimentation.

## Quick Start

### Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Directory Location

Most local CLI data is stored under `FERN_HOME`, which defaults to `~/.fern`.

```bash
export FERN_HOME=/path/to/custom/fern-home
```

### Start a Relay

```bash
fern-relay
```

The relay prints the address it is listening on. By default it uses the local host and a configurable port.

### CLI Client

```bash
fern init
fern whoami
fern group create --name "My Group"
fern group join <group-pubkey>
fern post <group> "Hello"
fern read <group>
fern watch <group>
fern verify <group>
fern relay start
fern relay info
fern dag --db <path>
```

Most commands sync from the canonical relays first so they can work from current history.

### Bracken

Bracken is the browser client in `bracken/`.

```bash
cd bracken
npm install
npm run dev
```

Bracken is a zero-backend client: it signs events in the browser, stores data in IndexedDB, and connects directly to relays over WebSocket.

### Cleanup

```bash
./fern-wipe.sh
```

This removes local CLI and relay data.
