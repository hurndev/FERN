# FERN-BFT

FERN is a signed-event protocol and client for decentralized group chat. The
current implementation replaces the original transport/DAG design with one
Tendermint-style consensus chain per group. A group is replicated by an
equal-weight validator set; clients submit signed events to those validators
and display them as pending until a quorum commit finalizes their permanent
block position.

The group key still identifies the group and signs genesis. Users still own
their identities and sign every action. Validators order and certify those
actions, but cannot forge messages or administration events.

This branch is an experimental reference implementation, not production-ready
infrastructure. It deliberately stops when evidence is missing or quorum is
lost instead of choosing a recovery path that could fork finalized history.

## Protocol documents

- [bft-spec.md](bft-spec.md) is the concrete `fern-bft-1` wire and validation
  contract.
- [architecture.md](architecture.md) describes the current system, consensus
  flow, trust boundaries and failure behavior.
- [python-architecture.md](python-architecture.md) maps those rules to the
  Python packages, validator runtime, CLI and Bracken boundary.
- [tendermint-design.md](tendermint-design.md) is the architectural plan behind
  the rewrite.
- [BFT-NOTES.md](BFT-NOTES.md) records implementation choices, deviations and
  known limitations.
- [spec.md](spec.md) documents the incompatible historical DAG protocol.

## What is implemented

- Ed25519-signed events with per-author sequences and deterministic execution
- propose/prevote/precommit consensus with durable votes and locks
- nil voting, round changes, valid-round lock proofs and authenticated
  future-round catch-up
- quorum-certified event observation times
- atomic SQLite commits and full client-side chain verification
- multi-group validator processes with WebSocket gossip and subscriptions
- verified validator restart/background catch-up before voting resumes
- Bracken validator reconnection, verified catch-up, and subscription recovery
- signed ingress receipts, validator statuses, manifests and hosting evidence
- exact-checkpoint `SyncReady` admission for new validator epochs
- Python CLI workflows and a browser-only Bracken client

A standard validator set tolerating `f` Byzantine validators contains exactly
`3f+1` validators and commits with `2f+1` votes. Groups may instead use one,
two, or three validators in nonstandard unanimous mode: they declare `f=0` and
require every validator's vote. CLI and Bracken users are always warned while
participating in such a group. This mode is intended for development and small
tests, has no claimed Byzantine fault tolerance, and halts when any validator
is unavailable.

## Install

Python 3.11 or newer is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

If the checkout is on a filesystem without symbolic-link support, put the
virtual environment on a local disk instead (for example `~/fern-venv`).

For containerized validator and Bracken operation, see
[deploy/README.md](deploy/README.md).

CLI data uses `FERN_HOME` (default `~/.fern`). Validator data uses
`FERN_VALIDATOR_HOME` (default `~/.fern-validator`). Existing
`FERN_RELAY_HOME` and `~/.fern-relay` installations are detected for migration.

## Local small-set walkthrough

Initialize and run a development validator:

```bash
fern-validator init --name local --host 127.0.0.1 --port 8765
fern-validator run
```

Normal validator output records server startup, hosted groups, accepted
genesis checkpoints, finalized blocks, and validator-epoch changes. Add
`--verbose` to follow event ingress and consensus rounds without dumping every
peer message:

```bash
fern-validator --verbose
# Equivalent when naming the subcommand explicitly:
fern-validator run --verbose
```

In another terminal, create an identity and group:

```bash
fern init
fern whoami
fern group create --name "My Group" --validator ws://127.0.0.1:8765 --faults 0
fern group list
```

Use the returned group key with the normal chat commands:

```bash
fern post <group-key> "Hello"
fern read <group-key>
fern watch <group-key>
fern verify <group-key>
```

Prefix any client command with `fern --verbose` to show validator discovery,
signed statuses, sync source selection, commit verification, publication, and
ingress-receipt details. Event content and private key material are never
logged.

Inspect consensus state, recent finalized blocks, their event positions, and
pending events with:

```bash
fern chain <group-key>
fern chain 1 --no-sync
fern chain --db /path/to/validator.db
```

The default view shows the ten newest blocks. Use `--limit 0` for all blocks,
`--full-ids` for complete identifiers, or `--no-events` for a compact block
summary. The old `fern dag --db ...` spelling remains as a hidden compatibility
alias and prints a rename warning.

## Multi-validator groups

Start one independently keyed validator process per endpoint. You may pass one
to three `--validator` options for unanimous `f=0` testing, or exactly `3f+1`
endpoints for standard BFT operation. For example, an `f=1` group uses four
validators and commits with three. Small-set groups warn in both the CLI and
Bracken because every configured validator is required for progress.

Adding a new validator is a two-step, exact-checkpoint workflow. On the new
validator:

```bash
fern-validator config add-witness wss://trusted-validator.example <pubkey> \
  --operator independent-operator-name
fern-validator prepare 'fern:<group-key>@wss://validator-a.example,wss://validator-b.example' \
  --output sync-ready.json
```

After every new validator has produced a readiness file, an existing group
admin submits the complete replacement set:

```bash
fern group validator-update <group-key> \
  wss://validator-a.example wss://validator-b.example \
  wss://validator-c.example wss://new-validator.example \
  --faults 1 --readiness sync-ready.json
```

Readiness is bound to the block immediately before the transition. If the
group advances first, prepare again. `--manual` on `fern-validator prepare` is an
explicit local resource-admission override; it does not weaken chain
verification or consensus authorization.

## Bracken

Bracken lives in `bracken/`. It signs in the browser, stores verified commits
and pending events in IndexedDB, and connects directly to validator WebSockets.

```bash
cd bracken
npm install
npm run dev
```

The IndexedDB schema migration clears legacy DAG objects because they are not
valid `fern-bft-1` history.

Bracken writes connection, sync, pending-event, and finalized-block records to
the browser developer console. Development builds also include request-level
debug records. To enable those verbose records in a production build, run this
in the developer console and reload:

```js
localStorage.setItem('fern:verbose', 'true')
location.reload()
```

Set the value to `false` to return to lifecycle-only logging.

## Development checks

```bash
ruff check src cli tests
mypy src cli
pytest -q

cd bracken
npm run lint
npm run build
```

`./fern-wipe.sh` removes local CLI and validator data. Review it before use if
you have customized the data locations.
