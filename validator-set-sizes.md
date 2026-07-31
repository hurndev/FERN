# Choosing a Validator Set Size

Every FERN group chooses its own validator set, and the set may contain any
number of validators from 1 to 100. This guide explains what each size gives
you so you can pick one deliberately when creating a group or changing its
set.

## How quorum works

A block is committed when **more than two thirds** of the group's validators
agree on it:

```text
quorum q = ⌊2n/3⌋ + 1
```

You never configure a fault parameter yourself — the protocol derives
everything from the validator count. Two properties follow from the quorum:

- **Liveness** — the group keeps committing as long as at least `q`
  validators are online and behaving. Up to `n − q` validators may be
  offline, compromised, or malicious without stopping progress.
- **Safety** — committed history can never fork as long as at most
  `2q − n − 1` validators are malicious.

Between those two bounds the group does the safe thing: it **freezes rather
than forks**. If more validators misbehave than the liveness budget but fewer
than the safety budget, commits stop until the set recovers; finalized
history stays consistent either way.

## Sizes at a glance

| Validators | Quorum | Keeps running with up to | Stays fork-safe with up to malicious | Min online |
|---:|---:|---:|---:|---:|
| 1 | 1 | 0 | 0 | 100% |
| 2 | 2 | 0 | 1 | 100% |
| 3 | 3 | 0 | 2 | 100% |
| 4 | 3 | 1 | 1 | 75% |
| 5 | 4 | 1 | 2 | 80% |
| 6 | 5 | 1 | 3 | 83% |
| 7 | 5 | 2 | 2 | 71% |
| 8 | 6 | 2 | 3 | 75% |
| 9 | 7 | 2 | 4 | 78% |
| 10 | 7 | 3 | 3 | 70% |
| 11 | 8 | 3 | 4 | 73% |
| 12 | 9 | 3 | 5 | 75% |
| 13 | 9 | 4 | 4 | 69% |

The pattern continues to the protocol maximum of 100: sizes **4, 7, 10, 13,
…** (`3f+1`) tolerate `f` offline-or-malicious validators outright; the two
sizes after each step keep the same tolerance but add fork-safety margin.

## What the sizes mean in practice

### 1–3 validators — unanimous mode

Every commit requires every validator, so **one unavailable validator freezes
the group** and there is no fault tolerance. Useful for local development,
tests, and tiny personal groups where a temporary freeze is acceptable.
Clients display a persistent warning while you participate in a group this
small.

### 4 validators — the minimum production configuration

The smallest set with real fault tolerance: **one** validator may be offline
or malicious and the group keeps running. Three of four must be reachable
(75%). A solid default for a small group run by four independent people.

### 5–6 validators — safety margin, not extra uptime

These tolerate the same single failure as 4 validators — the quorum grows
with the set, so the offline allowance stays at one. What they add is
fork-safety: with 2 (at n=5) or 3 (at n=6) compromised validators the group
freezes instead of risking conflicting finalized history. Choose 5 or 6 when
you already have that many genuinely independent operators and want them all
in the set; don't choose them expecting better uptime than 4.

### 7 validators — two-fault tolerance

The next real step: **two** validators may be offline or malicious, and only
five of seven need to be online (71%). A good default for a serious community
that can find seven independent operators.

### 10, 13, and beyond

Three- and four-fault tolerance, each step costing three more validators.
Large public communities may want this; for most group chats the coordination
cost outweighs the benefit. Note the minimum-online fraction keeps falling
(70%, 69%, …), so very large sets are also more tolerant of routine outages.

## Rules of thumb

- **The real uptime steps are 4, 7, 10, 13.** In-between sizes add
  freeze-don't-fork margin, never extra fault tolerance.
- **Independence is what you're counting.** Four keys run by one person, on
  one host, or in one jurisdiction are one failure domain, not four
  validators. Spread operators across machines, networks, and where possible
  jurisdictions; the operator label on each validator exists to help you keep
  track of who runs what.
- **Every validator is a permanent obligation** — online, keys backed up,
  upgraded with the group. A bigger set is more coordination forever, not
  just at creation time.
- **The choice isn't final.** A `validator_update` event can add or remove
  validators at any time while the group has quorum, and under this quorum
  rule any resulting size is valid. Newly added validators synchronize the
  full history and prove readiness before activation — see
  [validator-hosting.md](validator-hosting.md).
- **When in doubt: 4 independent operators for a small group, 7 for a
  serious one, 1–3 only for testing.**

## Appendix: the formulas

```text
quorum            q = ⌊2n/3⌋ + 1
fault tolerance   f = ⌊(n−1)/3⌋
liveness budget   n − q        offline + malicious combined, group keeps committing
safety budget     2q − n − 1   malicious only, history cannot fork
```

The safety bound comes from quorum intersection: two quorums of `q` out of
`n` validators share at least `2q − n` members, and fork-safety requires that
intersection to contain at least one honest validator who cannot sign
conflicting commits. For sizes `n = 3f+1` the quorum equals `2f+1` and both
budgets equal `f`; for `n = 3f+2` and `n = 3f+3` the safety budget exceeds
the liveness budget by one and two respectively, which is exactly the
freeze-rather-than-fork margin the in-between sizes provide.
