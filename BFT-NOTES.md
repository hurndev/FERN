# FERN-BFT Implementation Notes

This file records decisions, deviations and intentionally incomplete work made
while implementing `tendermint-design.md`. It should be updated whenever the
implementation chooses one side of an open design question.

## Initial decisions

- The first wire version is `fern-bft-1` and is deliberately incompatible with
  legacy DAG event IDs.
- `ts` remains in a user event only for UI provenance. Certified observation
  time and block position are authoritative.
- Per-author sequence numbers replace DAG parents for replay protection.
- The validator set is equal-weight and may contain any number of
  validators from 1 to 100. The fault count and quorum are derived from the
  validator count: `f = floor((n-1)/3)` and `q = floor(2n/3) + 1` (a
  CometBFT-style more-than-two-thirds quorum). Sizes `3f+1` (4, 7, 10, …)
  have `q = 2f+1` with equal liveness and safety budgets; intermediate
  sizes keep the same liveness budget and add fork-safety margin (the group
  halts rather than forks beyond `f`). Sets of one to three validators fall
  out of the same formula with `f=0` and `q=n` (unanimous certificates);
  they have no claimed Byzantine fault tolerance, any unavailable validator
  halts consensus, and the CLI and Bracken always display a warning.
- The quorum rule was originally exact `n=3f+1` with `q=2f+1`, which made
  5- and 6-validator groups unrepresentable even though they are valid BFT
  configurations (same liveness as 4 validators, with extra fork-safety
  margin). Deriving the quorum from the set size removes the forbidden
  counts, subsumes the old `f=0` small-set special case, and matches
  deployed practice (CometBFT, Ethereum). The change is backward
  compatible: both rules agree on every previously valid set (1–3
  unanimous and `3f+1` sizes), so all existing history verifies identically
  and no protocol-version bump was needed. `validator-set-sizes.md` is the
  reference table for sizes, quorums, and fault budgets.
- Validator ordering is lexicographic by public key. Proposer selection rotates
  over that order by `(height + round - 1) mod n`.
- Consensus servers are called validators throughout the active implementation,
  CLI, Bracken, deployment files, and documentation. The executable is
  `fern-validator`, its default home is `~/.fern-validator`, and its key is
  `validator.key`. `fern-relay`, `FERN_RELAY_HOME`, and existing relay-named
  homes/keys remain migration aliases so upgrades do not create a new validator
  identity accidentally.
- A proposal with incomplete data, invalid state execution or uncertain lock
  proof receives a nil prevote. No permissive recovery path is provided.
- Consensus phase waits are condition-based. Inbound traffic wakes a phase to
  recheck its proposal or quorum condition but cannot end that phase by itself.
  This fixes a runtime liveness failure in which three local validators needed
  hundreds of rounds because partial observations and votes caused premature
  nil votes.
- Future-round catch-up uses the Tendermint `f+1` sender rule in both modes.
  Unanimous small sets declare `f=0`, so one authenticated sender is enough.
  Requiring every other validator caused restarted peers to replay every round
  a lone survivor had entered, leaving phases offset and delaying a recovered
  three-validator group for minutes. The earlier round-spam problem was instead
  caused by traffic-driven phase completion and remains fixed by condition-based
  phase waits. A Byzantine sender may disrupt liveness in small-set mode, which
  is already outside that mode's zero-fault assumption; unanimous finality and
  durable locking remain unchanged.
- Accepted governance wakes the batching gate immediately. It never mutates an
  active proposal or becomes authoritative early; if a round is already in
  progress, the governance event starts the next height without another normal
  batching delay. If candidate selection discards the only pending event, every
  validator independently rechecks its pending selection and returns to idle
  instead of advancing empty nil-vote rounds forever.
- Active validators perform verified catch-up at startup and periodically while
  running. A group engine pauses only after `f+1` distinct current validators
  sign statuses ahead of its local checkpoint. Missing commits are then checked
  contiguously through the normal client verifier before a replacement engine
  starts from the durable store. One Byzantine status cannot force repeated
  pauses, and catch-up never lowers the commit quorum or invents recovery when
  the epoch itself has lost quorum.
- Every newly added validator must sign readiness for the checkpoint directly
  before the transition block. This is stricter and simpler than accepting a
  loosely bounded historical checkpoint plus an unspecified tail protocol.
- Readiness preparation can be initiated remotely through the
  `request_readiness` WebSocket action as well as locally through
  `fern-validator prepare`. Remote preparation runs the identical policy-gated
  admission path (trusted-host threshold, byte budget, verified full-history
  sync) and is never a `manual` override; the WoT threshold is what prevents
  remote requests from conscripting a validator into storing arbitrary
  attacker-created history. Bracken's `/validator-add <url>` uses it to add a
  validator in one step and retries once with fresh readiness when the group
  advances between preparation and the update (exact-checkpoint binding makes
  the stale case detectable rather than dangerous). The action is
  rate-limited at 10 requests per IP per minute because it triggers a full
  verified history download.
- Operator notices (message-of-the-day) are signed side-channel objects
  served in the `metadata` response. They expire, are verified against
  the serving validator's key, and clients display them with a badge in
  the validator info panel. Permanent announcements go through ordinary
  admin chat messages.
- The group freezes if the active epoch loses quorum. Catastrophic owner or
  admin recovery is not implemented because it would weaken finalized safety.
- Logical history size is the exact cumulative UTF-8 canonical byte count of
  genesis and commits. It is part of manifests, status and readiness proofs.
- Validator status is signed and clients compare every reachable reported
  checkpoint they can place on their locally verified chain, including reports
  made at different heights.
- `fern chain` replaces the misleading DAG inspector and supports either a
  configured sync-first group or direct `--db` inspection. The old `dag` name
  is a hidden warning-emitting compatibility alias. Validator-set replacement
  is exposed only as `validator-update`.
- The top-level `fern` command uses a lazy Click group. Menu rendering uses
  static short-help metadata and imports only the selected command, avoiding
  eager WebSocket, SQLite, cryptography, and consensus imports from NAS-backed
  checkouts.
- Validator INFO logging covers lifecycle and finality. `--verbose` logs
  consensus transitions and accepted-event metadata, but deliberately omits
  individual peer votes, full event content and all private key material. The
  Python client uses the same opt-in model; Bracken emits structured lifecycle
  records in the browser and gates request detail behind its verbose setting.
- Bracken owns one connection supervisor per active-group validator. Unexpected
  closure moves the endpoint to `Reconnecting`, retries with exponential backoff
  capped at 30 seconds, re-synchronizes missed commits, re-verifies the endpoint
  key against finalized validator state, and re-subscribes before marking it
  connected. Group switches and logout abort the retry loops.

## Known design limitations to resolve

- Deterministic fair inclusion is not fully solved by Tendermint. The initial
  implementation uses bounded proposer queues and gossip. Honest proposer
  rotation gives eventual inclusion under synchrony, but validators cannot
  safely know every event a proposer should have included.
- Validator `operator` labels help display diversity but are self-asserted.
- Certified timestamps assume enough honest clocks are reasonably configured.
  They are not external trusted time.
- Full-history retention is enforced by validator behavior and verification,
  but capacity leases and graceful retirement are not yet a protocol.
- History transfer currently pages complete commits (up to 100 per request)
  rather than implementing a separate chunk/Merkle-inclusion protocol. Every
  page is still verified from genesis to the fixed manifest checkpoint.
- Hosting admission uses a signed manifest plus matching attestations from
  locally trusted, independently labelled active validators. Operator labels
  are local policy rather than on-chain proof of organizational independence.
- A prospective validator is expected to remain reachable after producing
  `SyncReady` so it can receive the transition commit. If it misses that commit,
  it must synchronize again; there is no background archival discovery layer.
- Genesis auto-hosting is enabled by default for development. Operators serving
  untrusted networks should initialize with `fern-validator init --closed` and use
  explicit admission.
- Peer transport is not mutually authenticated. Consensus, observation, commit
  and event objects are individually signed and group/epoch-bound, and peer
  ingress is rate-limited; authenticated channels remain future hardening.
- Expected malformed or cryptographically invalid client events receive a
  structured WebSocket error. Internal storage and programming failures remain
  uncaught at that boundary so operators do not mistake them for client input.
- The implementation is purpose-built for FERN and follows Tendermint's
  propose/prevote/precommit locking rules. It is not a fork of CometBFT. This
  intentionally differs from the design document's preference for a maintained
  consensus core because no suitable embeddable Python/multi-group core is
  available in the repository and the user explicitly requested a from-scratch
  implementation. The safety kernel is isolated and adversarially tested.

## Validation note

The Python implementation is covered by formatting, strict typing and tests in
this repository. The Bracken rewrite was source-audited, but this development
environment has no Node.js/npm/bun/deno executable, so its TypeScript compiler,
ESLint and Vite build could not be executed here. No runtime was installed
outside the workspace because that was not authorized.
