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
- [validator-hosting.md](validator-hosting.md) explains how validators host
  new groups and how new validators are admitted to existing ones.
- [python-architecture.md](python-architecture.md) maps those rules to the
  Python packages, validator runtime, CLI and Bracken boundary.
- [protocol-app-boundary.md](protocol-app-boundary.md) documents the separation
  between the protocol core and application modules (the chat app, plus a second
  example app in `examples/chess`).
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

A validator set may contain any number of validators from 1 to 100; the
fault count `f` and quorum `q` are derived from the validator count `n`
(`f = floor((n-1)/3)`, `q = floor(2n/3) + 1`). A four-validator group
tolerates one unavailable or Byzantine validator and commits with three
votes; seven validators tolerate two, ten tolerate three. Sizes between
those steps (5, 6, 8, 9, …) keep the same fault tolerance and add
fork-safety margin — see [validator-set-sizes.md](validator-set-sizes.md).
Groups of one to three validators run in unanimous mode (`f=0`, `q=n`):
every validator's vote is required, so they halt when any validator is
unavailable and carry no claimed Byzantine fault tolerance. CLI and Bracken
users are always warned while participating in such a group.

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

To show a short message to clients that connect to your validator (planned
maintenance, an upcoming shutdown), set an operator notice:

```bash
fern-validator notice "Down for maintenance Tuesday 14:00-16:00 UTC" --expires 2d
fern-validator notice            # show the active notice
fern-validator notice --clear    # remove it early
```

A running validator serves it on its next metadata read, with no restart.
Bracken shows a `!` badge on the validator and the notice in its info panel;
`fern validator info <url>` prints it. Notices are signed side-channel
objects: they never touch consensus or group history, and stop being served
when they expire.

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

Start one independently keyed validator process per endpoint. Any number of
endpoints is accepted: one to three create a unanimous `f=0` test group, and
four or more create a standard BFT group whose fault tolerance is derived
from its size (4 tolerates 1, 7 tolerates 2, 10 tolerates 3; sizes in
between add fork-safety margin rather than extra tolerance). For example, a
four-validator group commits with three votes. Small-set groups warn in both
the CLI and Bracken because every configured validator is required for
progress.

Adding a new validator is an exact-checkpoint workflow. From Bracken it is
one step: a group admin runs `/validator-add <new-validator-url>`. The new
validator applies its own admission policy (it refuses unless enough locally
trusted current validators already host the group), downloads and verifies
the full history, signs its `SyncReady` proof, and the client submits the
validator-set update — retrying once with fresh readiness if the group
advanced in between. The equivalent CLI workflow is two steps. On the new
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
In Settings, "Minimum required connections" limits publishing to a random
`f+1` subset of validators while keeping passive connections to the rest for
status monitoring — useful for large groups.

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
