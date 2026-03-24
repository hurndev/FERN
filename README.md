# FERN

FERN (Fault-tolerant Event Relay Network) is a messaging protocol designed for decentralised public group chats. Users and groups are not tied to any particular server, making it censorship resistant by design. Messages (events) associated with groups are published to multiple relay servers simultaneously, in a similar fashion to NOSTR, but with stronger guarantees around group integrity and message history.

Relay servers are intentionally dumb. They store and forward signed events, but have no authority over users and groups. Everything is verified locally by the client. Each group lives on a selection of 'canonical' relays, so if one relay goes down the group continues to exist. Groups can be instantly migrated to new relays, and message history will go with it.

Message history is structured as a DAG (Directed Acyclic Graph), a similar concept to a blockchain. Every message references the messages before it, making the history tamper-proof and fully verifiable by anyone. Censorship is always detectable as a visible gap  in the chain. Clients automatically heal divergent relays by redistributing any messages a relay is missing, so the full message history is maintained across all group relays without direct communication between them.

Much of FERN's design is inspired by [NOSTR](https://en.wikipedia.org/wiki/Nostr), but unlike NOSTR it is designed specifically for group messaging: groups have a canonical relay set (rather than relying on a single centralized relay), history is verifiable for completeness across relays, and the self-healing replication model means a group's full history is always recoverable as long as any one client has it cached.

The full protocol spesification (WIP) can be found in [FERN-protocol-spec.md](FERN-protocol-spec.md)

## Current State

This repository contains a CLI client, relay server, chat webapp, DAG inspector, and some basic testing utilities. The implementation is primerally vibe-coded and has not been thoroughly tested. The core protocol is implemented, but has not been properly tested yet and is NOT ready for real use. For now I recommend only testing this locally.

## Quick Start

### Install
`python3 -m venv .venv && source .venv/bin/activate`

`pip3 install -e .`
#### Start a relay
`fern-server --port 8787`
#### Generate identity
`fern keygen`
#### Create a group
`fern create --name "My Group" --relay ws://localhost:8787`
#### Send a message
`fern send <group-pubkey> -m "Hello world"`
#### View DAG in browser
`fern-inspect`
#### Start webapp
`fern-chat`
