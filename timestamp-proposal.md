# Timestamp Manipulation: Analysis and Proposed Solutions

## 1. The Problem

An attacker can post a single event with a fabricated far-future timestamp (e.g., `ts = 2147483647`). The `ts >= max(parent.ts)` ratchet (added to fix Bug 2 in `ordering-and-timestamps.md`) then forces every subsequent event to have an equal or greater `ts`. Since no honest client can produce such a timestamp, the group is frozen: no new events can be accepted. This requires one event from one identity. In a public group, banning the attacker doesn't help — a new key can join immediately (sybil).

The attack is trivial to execute and effectively permanent until wall-clock time catches up to the fabricated timestamp.

---

## 2. What `ts` Is Actually Used For

The `ts` field is load-bearing in three places:

| Use | Where | Why it matters |
|---|---|---|
| **Canonical linearisation order** | `derive_group_state()` — sorts events by `(ts, id)` | Determines which events are applied first to state; determines conflict resolution (last-writer-wins) |
| **Causality ratchet** | Semantic validation — `ts >= max(parent.ts)` | Rejects events whose `ts` is less than their parent's `ts`; this is the rule the freeze attack exploits |
| **Ban expiry** | `is_banned_at(pubkey, ts)` — compares `until > ts` | Time-limited bans are evaluated against the event's `ts`, not wall-clock |

The root cause of the freeze is that **`ts` is self-reported by clients but used as a load-bearing input to state derivation**. The ratchet makes `ts` a monotonic ratchet — it can only go up — and any client can set it arbitrarily high. The combination is a denial-of-service.

---

## 3. Why the Existing Options Fall Short

| Option | Core problem |
|---|---|
| **A: Relay-side bound** | Gives relays a wall-clock validation role they were never meant to have. Relay clock skew causes inconsistent acceptance across relays, generating false group-status divergence noise (censorship-detection false positives). A compromised or fast-clocked relay still admits the event. |
| **B: Drop the ratchet** | Fixes the freeze minimally, but `ts` remains the linearisation key. Conflict resolution stays manipulable (set high `ts` to win). The same-ts parent/child inversion (Bug 1) still requires a convergence loop. Display ordering remains a manipulable surface. |
| **C: Use event_receipt.ts** | Receipts are author-local and not in the DAG. A new client syncing from genesis cannot recompute the order. Breaks "verify independently from genesis." |
| **D: Client policy** | Not a protocol guarantee. The event is still in the DAG, still propagated by relays, still affects clients that don't apply the policy. An attacker only needs to fool some clients. |

The deeper issue with all four is that they try to **bound `ts`** rather than questioning whether `ts` should be load-bearing at all.

---

## 4. The Core Insight

FERN already has a manipulation-resistant ordering: **the DAG**. Every event's `parents` array is signed by the author and verifiable by anyone. The DAG encodes causal happened-before relationships — parents were known to the author at event creation time. This structure is:

- **Unforgeable**: you can't fabricate a parent reference without controlling the author's key.
- **Verifiable**: any client can check the full parent chain from genesis.
- **Deterministic**: the DAG structure is identical on every client with the same event set.

Using self-reported `ts` as the linearisation key was redundant with the DAG, and it introduced a vulnerability the DAG does not have.

---

## 5. Solution A: Topological Linearisation (Primary Recommendation)

### 5.1 The Idea

Replace `(ts, id)` canonical linearisation with **deterministic topological order**: process parents before children; among concurrent events (all parents already placed), pick the smallest event ID. Drop the `ts >= max(parent.ts)` ratchet entirely. `ts` becomes a display-only field with no bearing on state derivation, conflict resolution, or event acceptance.

### 5.2 Algorithm

Uses Kahn's algorithm with an event-ID min-heap:

```
placed = {genesis}
ready = min-heap of events whose all parents are in placed,
        keyed by event ID (lexicographic ascending)

while ready is not empty:
    event = pop smallest ID from ready
    validate(event)                    # semantic + authorization checks
    if valid:
        apply_event(state, event)      # update group state
        placed.add(event)
        for each child of event:       # events that list this event as a parent
            if all parents of child are in placed:
                push child into ready
    else:
        reject(event)                  # event stored but not applied
```

Events whose parents are never placed (because a parent was rejected or is missing) never become ready. They are stored as disconnected/rejected, same as today.

### 5.3 What It Fixes

**The freeze attack is eliminated.** There is no ratchet. An event with `ts = 2147483647` is accepted (or rejected for other reasons — authorization, semantic validity), but its timestamp has no effect on any other event's acceptance or ordering. The attacker's event becomes a dangling tip that no one parents on. The group continues normally.

**Bug 1 (same-ts parent/child inversion) is eliminated.** Topological order guarantees parents are placed before children, always. The same-ts inversion that necessitated the convergence loop cannot occur. The primary trigger for the convergence loop disappears.

**Conflict resolution becomes manipulation-resistant.** Among concurrent events, the tiebreak is event ID (a SHA-256 hash), not `ts`. An attacker who wants to win a conflict (e.g., two concurrent bans on the same user) must grind event content to produce a higher hash. This is computationally expensive (birthday-bound) and low-stakes (the "prize" is winning one state race). In the current `(ts, id)` scheme, winning a conflict is free — just set `ts` higher.

### 5.4 What It Preserves

| FERN property | Status |
|---|---|
| Author signatures defeat forgery | Unchanged. Events, IDs, and signatures are identical. |
| Relays are dumb infrastructure | Unchanged. Ordering is computed client-side. Relays have no new role. |
| Deterministic state derivation | Improved. Same event set produces the same topological order on every client. No convergence-loop ambiguity. |
| Censorship detection | Unchanged. `set_hash`, `tips`, receipts, monitor, heal, fraud proofs are all structural — independent of ordering. |
| Moderation at display layer | Unchanged. Bans, kicks, and admin actions remain state events applied in deterministic order. |
| DAG completeness propagation | Unchanged. Parents/heads/gaps are structural. |
| No relay-to-relay communication | Unchanged. |
| 1-2 bad relays caught | Unchanged. |

### 5.5 What Changes

**Conflict resolution tiebreaker.** When two concurrent events affect the same state field (e.g., two `admin_remove` events targeting the same pubkey), the event with the higher ID (lexicographic) is applied second and wins (last-writer-wins). Previously, the higher `ts` won. The practical effect: conflict outcomes are determined by a hash rather than a self-reported value. This is more resistant to manipulation but means conflict winners are no longer correlated with wall-clock time.

**Display ordering.** Chat messages sorted by `ts` for display can be out of causal order (a child message might have a lower `ts` than its parent). This is cosmetic, not a state issue. Clients can address it by:
- Sorting display by topological order (causally correct, manipulation-resistant).
- Sorting display by `ts` but clamping each message's display `ts` to at least `max(parent display ts)`.
- Sorting by `ts` and accepting minor display anomalies from clock skew.

**The convergence loop becomes unnecessary.** Since topological order always places parents before children, the main trigger for the convergence loop (Bug 1 — child processed before parent due to same-ts inversion) cannot occur. The loop may be retained as a safety net for a rare edge case: concurrent semantic dependencies (e.g., a `chat.message` whose channel was created by a concurrent `chat.channel_create` that the message doesn't parent on). In practice this case is unlikely — a client composing a message in a new channel would naturally parent on the channel-creation event. Removing the loop simplifies the implementation; keeping it adds robustness at negligible cost.

### 5.6 Ban Expiry Interaction

With topological linearisation, `ts` is no longer used for ordering, but `is_banned_at(pubkey, ts)` still compares `until > event.ts`. This means a banned user can still set `ts >= until` to bypass a time-limited ban. This is a **pre-existing weakness** (see Section 7), not introduced by this change. Permanent bans (`until = null`) are unaffected — they are evaluated without reference to `ts`.

The topological fix and the ban-expiry weakness are orthogonal. Either can be addressed independently.

### 5.7 Implementation Surface

Changes required:

| File | Change |
|---|---|
| `src/fern/state/machine.py` | Replace `(e.ts, e.id)` sort with Kahn's algorithm. Remove `ts < max_parent_ts` checks (lines 208-211, 233-236). Optionally remove convergence loop (lines 225-250). |
| `bracken/src/fern/state.ts` | Same changes in TypeScript (lines 270-273, 284-289, 307-337). |
| `src/fern/events/semantic.py` | No change needed (the ratchet is in `machine.py`, not here). |
| `spec.md` §3.6 | Remove item 3 (timestamp validation: `ts >= max(parent.ts)`). |
| `spec.md` §8.3 | Replace `(ts, id)` linearisation with topological order description. Remove convergence-loop requirement. Update conflict resolution to describe ID-based tiebreak. |
| `spec.md` §8.6 | Update conflict resolution description. |
| Tests | Update `test_state.py` convergence/linearisation tests. Add topological ordering tests. Update property tests. |

---

## 6. Solution B: Drop the Ratchet Only (Minimal Alternative)

### 6.1 The Idea

Remove the `ts >= max(parent.ts)` rejection rule. Keep `(ts, id)` linearisation and the convergence loop. `ts` remains the ordering key but no longer causes event rejection.

### 6.2 What It Fixes

The freeze attack is eliminated: an event with `ts = 2147483647` is no longer rejected on ratchet grounds. Honest events with `ts = now` are accepted even if their parent has `ts = 2147483647` — the `(ts, id)` sort simply places the honest event before the attacker's event. The group continues.

### 6.3 What It Does Not Fix

- **Conflict resolution remains manipulable.** Setting high `ts` still wins same-field conflicts among concurrent events.
- **The convergence loop remains necessary.** Same-ts parent/child inversions still occur; Bug 1's fix (the loop) is still required.
- **Display ordering remains manipulable.** A malicious user can set `ts` to appear at any position in the message list.

### 6.4 Implementation Surface

| File | Change |
|---|---|
| `src/fern/state/machine.py` | Remove lines 208-211 and 233-236 (`ts < max_parent_ts` checks). |
| `bracken/src/fern/state.ts` | Remove lines 284-289 and 316-320 (same checks). |
| `spec.md` §3.6 | Remove item 3 (timestamp validation). |
| Tests | Update any tests that assert the ratchet rejects events. |

~4 lines removed per implementation. The convergence loop and `(ts, id)` sort stay as-is.

---

## 7. Orthogonal Issue: Ban Expiry Manipulation

### 7.1 The Problem

`is_banned_at(pubkey, ts)` evaluates `entry.until > ts` where `ts` is the event's self-reported timestamp (`types.py:39-45`). For permanent bans (`until = null`), this is secure — the check always returns `true` regardless of `ts`. For time-limited bans (`until = T`), a banned user can set their event's `ts >= T` to evade the ban. This is possible **today**, independently of the freeze attack.

The ratchet (`ts >= max(parent.ts)`) does not prevent this: the banned user sets `ts = max(parent.ts, T)` and the ratchet is satisfied.

### 7.2 Why It's Separate

Ban-expiry manipulation is a consequence of using self-reported `ts` for time-based policy evaluation. The freeze attack is a consequence of using self-reported `ts` for a monotonic ratchet. They share a root cause (untrusted `ts`) but are different attack surfaces with different severity:

- **Freeze**: affects the entire group, prevents all posting. One event, permanent damage.
- **Ban evasion**: affects one user, allows them to post despite a time-limited ban. Requires the banned user to actively exploit.

Permanent bans are immune to both. Time-limited bans are only vulnerable to evasion.

### 7.3 Options

**Option 1: Make `until` advisory, enforcement via explicit `unban` only.**
Drop time-based automatic expiry from the protocol's ban semantics. A ban persists until an `unban` event is issued. The `until` field remains in the `ban` event content as a display hint (e.g., "this ban was intended to expire at X") but has no effect on state derivation. This is the simplest fix: it eliminates the attack surface entirely at the cost of requiring an explicit `unban` to lift every ban. Admins who want time-limited bans can set a reminder and issue `unban` manually, or a bot can issue it.

**Option 2: Evaluate ban expiry against the parent's `ts` rather than the event's `ts`.**
When checking whether a ban is active for event E, use `max(parent.ts)` instead of `E.ts`. This is manipulation-resistant because `max(parent.ts)` is derived from the DAG (signed by other authors), not self-reported. The banned user cannot set `max(parent.ts)` — it's determined by the events they're building on. This preserves time-limited ban semantics without trusting the event author's clock. Requires the ratchet rule to be meaningful (parents have lower or equal `ts`), so this option pairs naturally with keeping the ratchet (but removing its *rejection* behavior — it only serves as a bound for ban checks). If the ratchet is dropped entirely (Solution A or B), this option needs the parent-ts to be available but doesn't require the ratchet to be enforced.

**Option 3: Accept the weakness.** Permanent bans are the real security boundary; time-limited bans are a convenience feature with known limitations. Document this and move on.

### 7.4 Recommendation

Option 3 (accept) is the most pragmatic. The freeze attack is critical; ban-expiry manipulation is minor and already partially mitigated (permanent bans are immune). If the team later wants to harden time-limited bans, Option 1 (advisory `until`) is the simplest path and aligns with FERN's philosophy of minimal relay/client complexity.

---

## 8. Comparison

| | Solution A: Topological | Solution B: Drop ratchet |
|---|---|---|
| **Freeze attack** | Eliminated | Eliminated |
| **Convergence loop** | Unnecessary (removed or kept as safety net) | Still required (Bug 1 persists) |
| **Conflict resolution** | Manipulation-resistant (ID-based hash tiebreak) | Manipulable (ts-based) |
| **Display ordering** | Needs client-side policy (ts is display-only) | Same as today |
| **Implementation size** | Medium — new sort algorithm + loop removal + spec rewrite of §8.3 | Small — remove ~4 lines/impl + spec tweak |
| **Test changes** | Significant — linearisation tests rewritten, convergence tests updated | Moderate — ratchet-rejection tests removed |
| **Spec changes** | §3.6, §8.3, §8.6, §15.5 all rewritten | §3.6 item 3 removed |

---

## 9. Recommendation

**Solution A (topological linearisation)** is the stronger fix. It eliminates the freeze attack *and* removes the convergence loop's primary purpose *and* hardens conflict resolution — all by replacing a self-reported field with a verifiable structural property the protocol already has. It is a medium-sized change to the state derivation layer but touches no other protocol surface (completeness layer, relay protocol, transport, identity, discovery are all unaffected).

**Solution B** is a correct minimal fix if the team wants to resolve the freeze quickly and revisit ordering later.

Both can be implemented independently. If Solution B is shipped first, Solution A can be applied later as a follow-up without any protocol-breaking change (the final state is the same; only the derivation order differs, and the spec can be updated in stages).

The ban-expiry weakness (Section 7) is orthogonal and can be addressed on a separate timeline. The most pragmatic path is to accept it and document it, with permanent bans as the recommended security boundary.
