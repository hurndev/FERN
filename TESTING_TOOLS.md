# FERN Testing Guide

## Test User Management

```bash
# Create a test user in /tmp/<name>
fern-test spawn-user alice

# List all test users in /tmp
fern-test list-users

# Delete all test users in /tmp
fern-test wipe-users --yes
```

## Relay Inspection

```bash
# Get event count and tips from a relay
fern-debug relay summary <group_pubkey> --relay ws://localhost:8787

# Fetch a specific event by ID
fern-debug relay get <event_id> --relay ws://localhost:8787

# Compare event sets across relays
fern-debug compare-relays <group_pubkey> --relay ws://localhost:8787 --relay ws://localhost:8788
```

## Event Injection

```bash
# Publish a raw (or tampered) event directly to a relay
fern-debug publish-raw ws://localhost:8787 '{"id":"...","type":"message",...}'
```

Use this to test signature validation, field tampering, etc.

## Multi-User Sending

```bash
# Have multiple users each send one message
fern-test multi-send <group_pubkey> alice bob carol --relay ws://localhost:8787

# Send 5 messages per user concurrently
fern-test multi-send <group_pubkey> alice bob --concurrent --count 5
```

Users must exist in `/tmp/<username>`. Use `eval $(fern-test spawn-user alice)` first.

## Real-Time Watching

```bash
# Watch events on a relay
fern-test watch <group_pubkey> --relay ws://localhost:8787
```

## DAG Inspection

```bash
# Show DAG as tree
fern-debug dag-tree <group_pubkey>

# Show derived group state
fern-debug state <group_pubkey>

# Full health check
fern-debug dag-health <group_pubkey>

# List gaps
fern-debug gaps <group_pubkey>
```

## Network Partitions

The `partition` command is a placeholder that prints manual instructions. For real partition testing:

```bash
# Stop a relay
kill <pid>

# Restart it
fern-server serve --port 8787 --storage /tmp/relay1
```

## Workflow Example

```bash
# Terminal 1: Start relay
fern-server serve --port 8787

# Terminal 2: Create test users (auto-stored in /tmp/<name>)
fern-test spawn-user alice
fern-test spawn-user bob

# Terminal 3: Set test user and create a group
export FERN_TEST_USER=alice
fern create --name "Test Group" --relay ws://localhost:8787

# Terminal 4: View DAG in browser (auto-uses /tmp/alice)
export FERN_TEST_USER=alice
fern-inspect

# Terminal 5: Send messages (auto-uses /tmp/alice)
export FERN_TEST_USER=alice
fern send <group> -m "Hello from Alice" --relay ws://localhost:8787

# Concurrent sends from multiple users
fern-test multi-send <group> alice bob --concurrent --count 10
```

**Note:** When `FERN_TEST_USER` is set, all `fern`, `fern-chat`, and `fern-inspect` commands automatically use `/tmp/<name>` storage and print `[TEST USER]` on startup.
