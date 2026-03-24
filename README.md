# FERN

FERN (Fault-tolerant Event Relay Network) is a messaging protocol designed for decentralised public group chats. Users and groups are not tied to any particular server, making it censorship resistant by design. Messages (events) associated with groups are published to multiple relay servers simultaneously, so the group continues to exist even if some relays go offline.

Relay servers are intentionally dumb. They store and forward signed events, but have no authority over users and groups. Everything is verified locally by the client. Each group lives on a selection of 'canonical' relays, so if one relay goes down the group continues to exist. Groups can be instantly migrated to new relays, and message history will go with it.

Message history is structured as a DAG (Directed Acyclic Graph), a similar concept to a blockchain. Every message references the messages before it, making the history tamper-proof and fully verifiable by anyone. Censorship is always detectable as a visible gap  in the chain. Clients automatically heal divergent relays by redistributing any messages a relay is missing, so the full message history is maintained across all group relays without direct communication between them.

Much of FERN's design is inspired by [NOSTR](https://en.wikipedia.org/wiki/Nostr), but unlike NOSTR it is designed specifically for group messaging: groups have a canonical relay set (rather than relying on a single centralized relay), history is verifiable for completeness across relays, and the self-healing replication model means a group's full history is always recoverable as long as any one client has it cached.

The full protocol specification (WIP) can be found in [FERN-protocol-spec.md](FERN-protocol-spec.md)

## Current State

This repository contains a CLI client, relay server, chat webapp, DAG inspector, and some basic testing utilities. The implementation is primerally vibe-coded and has not been thoroughly tested. The core protocol is implemented, but has not been properly tested yet and is NOT ready for real use. For now I recommend only testing this locally.

## Quick Start

### Install
```bash
python3 -m venv .venv && source .venv/bin/activate
pip3 install -e .
```

### Directory Location

All tools store data in a `.fern` directory. By default this is `~/.fern`. You can customise the location:

- ** `--home <path>` ** - Specify a custom `.fern` parent directory (e.g. `fern --home /tmp/me chat`)
- ** `FERN_TEST_USER` env var** - Set to any value to use `/tmp/<your-username>/.fern` instead

Priority order: `--home` > `FERN_TEST_USER` > `~/.fern`

### Start a Relay
```bash
fern-server
```
You can specify a custom storage location with `--storage <path>`, and a port with `--port`. Useful for running several relays on one machine.

### CLI Client (`fern`)

```bash
fern keygen                    # Generate Ed25519 identity keypair
fern profile                   # Display your public key
fern create --name "My Group" --relay ws://localhost:8787  # Create a group
fern groups                    # List all known groups
fern send <group-pubkey> -m "Hello"  # Send a message
fern messages <group-pubkey>   # Show messages in a group
fern subscribe <group-pubkey>  # Subscribe and display messages in real-time
fern sync <group-pubkey>       # Sync from canonical relays, healing divergences
fern invite <group-pubkey> <user-pubkey>  # Invite a user (mod only)
fern join <group-pubkey>       # Join a group you've been invited to
fern leave <group-pubkey>      # Leave a group
fern kick <group-pubkey> <user-pubkey>  # Kick a user (mod only)
fern relay-update <group-pubkey> --add ws://relay2:8787  # Update canonical relays
fern wipe                      # Delete all local data, keep keypair
```

### Web Chat (`fern-chat`)

```bash
fern-chat --port 8080
```

Opens the chat webapp. Experimental, history sync is currently broken.

### DAG Inspector (`fern-inspect`)

```bash
fern-inspect
```

Opens a visualizer showing the event DAG structure. You can specify a home directory with `--home <path>`.
