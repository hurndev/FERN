# FERN

FERN (Fault-tolerant Event Relay Network) is a messaging protocol designed for decentralised public group chats. Users and groups are not tied to any particular server, making it censorship resistant by design. Messages (events) associated with groups are published to multiple relay servers simultaneously, so the group continues to exist even if some relays go offline.

Relay servers are intentionally dumb. They store and forward signed events, but have no authority over users and groups. Everything is verified locally by the client. Each group lives on a selection of 'canonical' relays, so if one relay goes down the group continues to exist. Groups can be instantly migrated to new relays, and message history will go with it.

Message history is structured as a DAG (Directed Acyclic Graph), a similar concept to a blockchain. Every message references the messages before it, making the history tamper-proof and fully verifiable by anyone. Censorship is always detectable as a visible gap in the chain. Clients automatically heal divergent relays by redistributing any messages a relay is missing, so the full message history is maintained across all group relays without direct communication between them.

Much of FERN's design is inspired by [NOSTR](https://en.wikipedia.org/wiki/Nostr), but unlike NOSTR it is designed specifically for group messaging: groups have a canonical relay set (rather than relying on a single centralized relay), history is verifiable for completeness across relays, and the self-healing replication model means a group's full history is always recoverable as long as any one client has it cached.

The full protocol specification (WIP) can be found in [FERN-protocol-spec.md](FERN-protocol-spec.md)

## Current State

This repository contains a CLI client, relay server, Qt desktop chat application, DAG inspector, debug tools, and testing utilities. The core protocol is implemented but has not been thoroughly tested and is NOT ready for production use. For now, only test locally.

## Quick Start

### Install
```bash
python3 -m venv .venv && source .venv/bin/activate
pip3 install -e .
```

### Directory Location

All tools store data in a `.fern` directory. By default this is `~/.fern`. You can customise the location:

- `--home <path>` — specify a custom `.fern` parent directory
- `FERN_TEST_USER` env var — set to any value to use `/tmp/<your-username>/.fern` instead

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
fern create --name "My Group"  # Create a group (prompts for relays)
fern create --name "My Group" --relays ws://relay1:8787,ws://relay2:8787  # Create with specific relays
fern groups                    # List all known groups (from known relays)
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

Most commands perform a sync first to get the latest event history.

### Qt Chat App (`fern-chat`)

```bash
fern-chat
```

Opens a Qt desktop window with a retro-style interface. You'll need at least one relay running to create or join groups.

### DAG Inspector (`fern-inspect`)

```bash
fern-inspect
```

Opens a visualizer showing the event DAG structure. You can specify a home directory with `--home <path>`.

### Debug Tools (`fern-debug`)

```bash
fern-debug relay summary <group_pubkey> --relay ws://localhost:8787  # Check relay state
fern-debug dag-tree <group_pubkey>                                    # Print DAG as text tree
fern-debug state <group_pubkey>                                       # Show derived group state
fern-debug gaps <group_pubkey>                                        # List missing events
fern-debug compare-relays <group_pubkey> --relay ws://localhost:8787  # Compare relays
fern-debug publish-raw <relay> '{"id":"...","type":"message",...}'    # Inject raw events
```

### Test Harness (`fern-test`)

```bash
fern-test spawn-user alice          # Create test user in /tmp/alice
fern-test multi-send <group> alice bob --concurrent  # Send from multiple users
fern-test watch <group_pubkey>      # Watch events on a relay in real-time
```
