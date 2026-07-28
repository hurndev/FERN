# FERN-BFT Deployment

This directory contains Docker Compose projects for a FERN-BFT validator and
the Bracken web client. They can be deployed independently. A production group
normally uses four or more validators on independently administered hosts
(4, 7, 10, … tolerate 1, 2, 3 faults; any size is accepted, with the fault
count and quorum derived from the set size); this stack
runs one validator identity per `deploy/validator` deployment. One to three
validators are accepted only as unanimous `f=0` development/test groups; all
clients warn because every validator is required for progress.

The service is an active consensus validator with full history.

```text
deploy/
├── .env.example          shared build and volume settings
├── validator/
│   ├── Dockerfile
│   ├── compose.yml
│   ├── config.example.json
│   └── data/             validator config, key, and SQLite history
└── bracken/
    ├── Dockerfile
    ├── compose.yml
    └── nginx-bracken.conf
```

## Prerequisites

- Docker 20 or newer with Compose V2;
- a host directory writable by UID/GID `1000:1000` for validator data;
- a TLS-terminating reverse proxy that supports WebSocket upgrades;
- one public hostname per validator and, optionally, another for Bracken.

Use `wss://` validator URLs outside a trusted local network. Consensus objects
are individually signed, but the current peer transport is not mutually
authenticated.

## Configure the Compose projects

From the repository root:

```bash
cp deploy/.env.example deploy/.env
$EDITOR deploy/.env

ln -sf ../.env deploy/validator/.env
ln -sf ../.env deploy/bracken/.env
```

Set `VITE_VALIDATOR_URL` to one or more public validator endpoints separated by
commas or spaces. Bracken uses them as create-group defaults and discovery
hints. A Byzantine-tolerant `f=1` group needs exactly four independently keyed
validators. One to three endpoints create a unanimous development/test group
with `f=0`; Bracken displays a warning and requires every validator for each
commit.

`FERN_DATA_DIR` is the host path bind-mounted at `/data` in the validator
container. The default is `deploy/validator/data` when Compose is run from that
directory.

For an existing checkout that already has `deploy/relay/data`, do not
reinitialize it. Set `FERN_DATA_DIR=../relay/data` while migrating, or move the
directory only after stopping the old container and taking a backup. Existing
configs that name `/data/relay.key` remain valid; the validator deliberately
reuses that key instead of generating a new identity.

## Initialize a validator

Build the image and initialize the persistent key and paths before the first
start:

```bash
cd deploy/validator
docker compose build
docker compose run --rm validator \
  --config /data/config.json init \
  --name "My FERN Validator" \
  --store /data/validator.db \
  --closed
docker compose up -d
cd ../..
```

`--closed` disables automatic hosting of arbitrary genesis events. It is the
recommended setting on a public endpoint. Omit it only for a development
validator that should accept new groups directly from clients.

Initialization creates:

- `/data/config.json`, the validator and admission configuration;
- `/data/validator.key`, the validator's Ed25519 private key;
- `/data/validator.db`, created on first run for histories and safety state.

Back up `validator.key` immediately and never replace it for an active validator.
The public key is committed into every group's validator set. Losing it can
remove that validator's vote and freeze any group that can no longer reach its
derived quorum (all validators for a small set, or more than two thirds of
the set in standard mode).
Copy the SQLite database consistently as well;
it contains the durable vote and lock journal that prevents double signing
after restart.

The container command defaults to:

```text
fern-validator --config /data/config.json
```

With no subcommand, `fern-validator` runs the validator.

## Start Bracken

After setting `VITE_VALIDATOR_URL`:

```bash
cd deploy/bracken
docker compose up -d --build
cd ../..
```

The validator hints are compiled into the browser bundle, so changing them
requires a Bracken rebuild. Finalized validator URLs come from each group's
verified on-chain state; the build-time values are only starting points.

Bracken stores identities, verified commits, pending events, and ingress
receipts in each user's browser IndexedDB. The nginx container has no client
state to back up.

## Reverse proxy integration

Neither Compose project publishes a host port by default. Choose one of these
patterns.

### Attach the proxy container to the Compose networks

The projects create `fern-validator` and `fern-bracken` Docker networks:

```bash
docker network connect fern-validator nginxproxymanager
docker network connect fern-bracken nginxproxymanager
```

Configure the proxy targets as:

- `validator.example.com` -> `fern-validator:8765`, with WebSockets enabled and no
  response caching;
- `chat.example.com` -> `fern-bracken:80`, with ordinary static-asset caching.

The public validator URL committed into a group must be the externally
reachable `wss://validator.example.com`, not the container hostname.

### Publish loopback ports

If the reverse proxy runs directly on the host, add loopback-only port mappings
to the relevant Compose services:

```yaml
# deploy/validator/compose.yml
services:
  validator:
    ports:
      - "127.0.0.1:8765:8765"

# deploy/bracken/compose.yml
services:
  bracken:
    ports:
      - "127.0.0.1:8080:80"
```

Proxy to `127.0.0.1:8765` and `127.0.0.1:8080`. Do not expose an unencrypted
`ws://` validator port directly to the public Internet.

### Reuse an existing Docker network

Replace the `networks` section in each Compose file if the proxy already uses a
shared external network:

```yaml
networks:
  default:
    name: npm_network
    external: true
```

The proxy can then reach the services by their container names.

## Multi-validator groups

Repeat the validator deployment on each independent host. Each process must
have its own `validator.key`, public `wss://` URL, and durable database. Confirm the
metadata endpoint through the CLI before creating a group:

```bash
fern validator info wss://validator-a.example.com
```

For production, create the group with four or more endpoints; the fault
tolerance is derived from the count (4 tolerates 1, 7 tolerates 2). For a
one-fault group:

```bash
fern group create --name "My Group" --faults 1 \
  --validator wss://validator-a.example.com \
  --validator wss://validator-b.example.com \
  --validator wss://validator-c.example.com \
  --validator wss://validator-d.example.com
```

Every validator must be able to make outbound WebSocket connections to the
other committed endpoints. Firewalls and proxies must allow long-lived inbound
client/peer connections and outbound peer traffic.

## History admission and adding a validator

Trusted-host configuration no longer authorizes DAG healing. It is local
resource policy for deciding which active validators may attest a fixed full-
history checkpoint before this process starts hosting a new group.

Add trusted history sources with independent operator labels:

```bash
docker exec fern-validator fern-validator --config /data/config.json \
  config add-witness wss://validator-a.example.com <validator-a-pubkey> \
  --operator operator-a
docker exec fern-validator fern-validator --config /data/config.json \
  config add-witness wss://validator-b.example.com <validator-b-pubkey> \
  --operator operator-b
```

The `witness` spelling is retained for CLI compatibility; these entries are
trusted active validators used only for local admission. The full JSON shape is
shown in `validator/config.example.json`.

To stage a group on the prospective validator, run:

```bash
docker exec fern-validator fern-validator --config /data/config.json prepare \
  'fern:<group-key>@wss://validator-a.example.com,wss://validator-b.example.com' \
  --output /data/sync-ready.json
```

Preparation verifies genesis and every commit through one signed manifest,
persists the history, and emits `SyncReady` for that exact checkpoint. Copy the
file to an existing group administrator and submit a complete replacement set:

```bash
fern group validator-update <group-key> \
  wss://validator-a.example.com \
  wss://validator-b.example.com \
  wss://validator-c.example.com \
  wss://new-validator.example.com \
  --faults 1 --readiness sync-ready.json
```

Bracken administrators can skip the file handoff: `/validator-add
wss://new-validator.example.com` asks the new validator to prepare remotely
through the policy-gated `request_readiness` action and submits the update
itself. The validator's configured trusted-host threshold still decides
whether it accepts hosting; a validator with no matching trusted witnesses
refuses the request. Either way,
if the group advances after preparation, generate readiness again. The
transition is invalid unless readiness covers exactly every newly added
validator and the block immediately before the transition.

## Configuration

Display the effective validator configuration with:

```bash
docker exec fern-validator fern-validator --config /data/config.json config show
```

The main sections are:

- `consensus`: block interval and phase/round timeout policy;
- `ingress`: event-submission rate limit;
- `history_admission`: trusted sources, minimum distinct operator labels, and
  maximum logical history size;
- `maximum_message_bytes`: WebSocket message limit;
- `allow_genesis`: open development bootstrap or closed operator admission.

Timeout and rate-limit changes are local policy. Validator membership,
quorums, event validity, and checkpoint roots come from verified group history
and cannot be overridden in this file.

## Backup and restore

Back up all three files in the mounted data directory:

- `config.json`;
- `validator.key`;
- `validator.db`, including all SQLite WAL data.

Stop the container or use a SQLite-aware snapshot/backup procedure before
copying the database. Restore the key, config, and database together before
starting the validator. Restoring an old database while retaining a newer copy
elsewhere risks operating from stale consensus safety state; never run two
instances with the same validator key.

## Operator notices

To show a short message to clients connecting to the validator (planned
maintenance, an upcoming shutdown), set an operator notice:

```bash
docker exec fern-validator fern-validator --config /data/config.json \
  notice "Down for maintenance Tuesday 14:00-16:00 UTC" --expires 2d
```

The running validator serves it on its next metadata read; no restart is
needed. Bracken displays a `!` badge on the validator and the notice in its
info panel, and `fern validator info wss://validator.example.com` prints it.
Clear it early with `notice --clear`. Notices are signed side-channel
objects: they never touch consensus or group history, and stop being served
when they expire.

## Updating and logs

```bash
git pull
cd deploy/validator && docker compose up -d --build && cd ../..
cd deploy/bracken && docker compose up -d --build && cd ../..
```

Run only the command for deployed components. Follow logs with:

```bash
cd deploy/validator && docker compose logs -f
cd deploy/bracken && docker compose logs -f
```

The validator logs startup, hosted groups, genesis, finalized blocks, and epoch
changes at the default level. To include accepted events and consensus stages,
temporarily add this override to the `validator` service and recreate it:

```yaml
services:
  validator:
    command: ["--config", "/data/config.json", "--verbose"]
```

Verbose output records round/proposer selection, candidate and proposal
construction, local prevote/precommit decisions, quorum transitions, catch-up,
and round advancement. It deliberately does not print every peer vote or full
event content.

After an update, `fern verify <group>` can independently re-check a client
cache from genesis. Monitor validator logs and signed statuses for stalled
heights or contradictory checkpoints.

## Cleanup

Stopping services preserves data:

```bash
cd deploy/validator && docker compose down
cd deploy/bracken && docker compose down
```

Deleting `FERN_DATA_DIR` destroys the validator key, full history, and safety
journal. Do not remove it for a validator that is still committed in a group.
The repository's `fern-wipe.sh` is for local CLI/validator data and is not a
Docker cleanup command.
