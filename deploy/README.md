# FERN Deployment

Docker-based deployment for the FERN relay and Bracken web client. Each
component is a self-contained Docker Compose project — run just the relay, just
Bracken, or both.

```
deploy/
├── .env.example          # shared env template
├── relay/                # relay-only stack
│   ├── Dockerfile
│   ├── compose.yml
│   ├── relay-entrypoint.sh
│   └── data/             # bind mount: relay.db + relay.key
└── bracken/              # bracken-only stack
    ├── Dockerfile
    ├── compose.yml
    └── nginx-bracken.conf
```

## Prerequisites

- Docker 20+ with Compose V2
- A TLS-terminating reverse proxy that can reach the compose networks
  (Nginx Proxy Manager, Caddy, nginx, Traefik, etc.)
- Two hostnames pointing at this box (e.g. `relay.example.com` for the relay,
  `chat.example.com` for Bracken). One is enough if you only deploy one
  component.

## First-time setup

```bash
# 1. Clone the repo to a stable location
git clone <repo-url> /opt/fern          # or wherever you keep it
cd /opt/fern

# 2. Create the shared env file
cp deploy/.env.example deploy/.env
$EDITOR deploy/.env
#   Set VITE_RELAY_URL to the public wss:// URL of the relay
#   (e.g. wss://relay.example.com)
#   FERN_DATA_DIR can stay at the default unless you want to move it.

# 3. Symlink the env file into each compose project directory.
#    Docker Compose looks for `.env` in the same directory as the compose
#    file when resolving ${VAR} in build args and volumes. The `env_file:
#    ../.env` directive only affects runtime service env, not parse-time
#    substitution. Symlinks keep a single source of truth.
ln -sf ../.env deploy/relay/.env
ln -sf ../.env deploy/bracken/.env

# 4a. Start just the relay
cd deploy/relay && docker compose up -d --build && cd ../..

# 4b. Start just Bracken
cd deploy/bracken && docker compose up -d --build && cd ../..
```

The first start of the relay generates a keyfile at `deploy/relay/data/relay.key`
and prints the new pubkey. **Back this file up** — losing it means a new
pubkey on the next restart, which invalidates client trust pins and stored
event_receipts (see spec §10.6).

## Reverse proxy integration

Neither compose file publishes host ports by default. Pick one of the
following integration patterns:

### Option A: Attach the proxy container to the compose networks (recommended)

The compose files create dedicated networks `fern-relay` and `fern-bracken`.
Attach your reverse-proxy container to whichever it needs to reach:

```bash
# Example: Nginx Proxy Manager
docker network connect fern-relay   nginxproxymanager
docker network connect fern-bracken nginxproxymanager
```

Then in the proxy UI:
- `relay.example.com` → forward to `fern-relay:8765`
  (WebSockets on, no caching, no static asset serving)
- `chat.example.com`  → forward to `fern-bracken:80`
  (WebSockets on, cache static assets on)

### Option B: Publish host ports and proxy to localhost

If your reverse proxy runs on the host (not in a container), edit each
`compose.yml` and uncomment/add:

```yaml
# relay/compose.yml
services:
  relay:
    ports:
      - "127.0.0.1:8765:8765"

# bracken/compose.yml
services:
  bracken:
    ports:
      - "127.0.0.1:8080:80"
```

The `127.0.0.1` binding means only the host can reach the port — not exposed
to the LAN. Then in the proxy UI, forward to `127.0.0.1:8765` and
`127.0.0.1:8080` respectively.

### Option C: Put the compose networks on an existing shared network

If you already have a Docker network your reverse proxy is on (e.g.
`npm_network`), edit the `networks:` block in each `compose.yml`:

```yaml
networks:
  default:
    name: npm_network
    external: true
```

This drops the compose-managed `fern-relay` / `fern-bracken` networks and
puts both services on `npm_network` directly. The proxy reaches them by
container name (`fern-relay:8765`, `fern-bracken:80`).

## Trusted-heal configuration

The relay supports **trusted-witness fast heal**, where missing events are
fetched from peer relays that countersign attestations, rather than relying
solely on rate-limited slow heal from clients.

To enable it:

1. Copy `deploy/relay/trust-config.example.json` to your data directory (e.g.
   `deploy/relay/data/trust-config.json`) and fill in the witness relay URLs
   and pubkeys.
2. Set `FERN_TRUST_CONFIG=/data/trust-config.json` in `deploy/.env`.
3. Restart the relay: `cd deploy/relay && docker compose up -d`.

Without a trust config the relay works normally — only slow heal (client-driven
re-request with rate limits) is available.

See `trust-config.example.json` for all options (thresholds, rate limits, batch
limits, quotas).

## Backup

The relay persists two files in `deploy/relay/data/` (or whatever
`FERN_DATA_DIR` points to):

- `relay.db` — SQLite event store (grow over time, back up periodically)
- `relay.key` — relay identity (64-char hex, back up *once* and keep safe)

Bracken is stateless: all client state lives in the user's browser
(IndexedDB). Nothing to back up on the server.

## Updating

```bash
git pull
cd deploy/relay   && docker compose pull && docker compose up -d --build && cd ../..
cd deploy/bracken && docker compose pull && docker compose up -d --build && cd ../..
```

(Run only the commands for components you've deployed.)

To change the relay URL Bracken connects to, edit `VITE_RELAY_URL` in
`deploy/.env` and rebuild Bracken (build arg is baked in at image build
time, not runtime).

## Logs

```bash
cd deploy/relay   && docker compose logs -f
cd deploy/bracken && docker compose logs -f
```

## Cleanup

```bash
cd deploy/relay   && docker compose down
cd deploy/bracken && docker compose down

# Nuclear: also delete images, build cache, and (for the relay) the data dir
docker image prune -f
rm -rf deploy/relay/data    # ⚠️ destroys relay identity + all stored events
```

The repo's `./fern-wipe.sh` is for the Python CLI/relay workflow, not the
Docker setup.
