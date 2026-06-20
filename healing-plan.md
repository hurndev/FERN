Complete Implementation Plan: Efficient Healing with Sync Coordination
Overview
Replace Bracken and CLI "download everything every time" sync with an attestation-gated, ID-diff-based healing flow, coordinated via sync locks to prevent thundering herd, and using a dedicated backfill action to avoid spamming subscribers during healing.
Three new WebSocket actions: sync_ids, sync_lock/sync_unlock, backfill
One relay optimization: dedup before expensive verification
One Bracken module: attestation verification + set_hash computation
Python transport/client support for the new actions
Rewritten sync flow for Bracken and CLI: attestation gate → sync lock → ID diff → targeted transfer
Part A: Python Relay Server Changes
File: src/fern/transport/websocket_server.py
A1: Relay-side dedup in _handle_publish
After parsing the event dict and before verify_event(event), add:
if event.id and await self._store.has_event(event.id):
    stored = await self._store.get_event(event.id)
    if stored is not None:
        receipt = build_receipt(
            event=stored,
            relay_keypair=self._keypair,
            ts=int(time.time()),
        )
        logger.info("publish duplicate id=%s... -> receipt (skipped verify/broadcast)", event.id[:16])
        return [{"type": "receipt", "receipt": {...same as existing...}}]
If the stored event can't be fetched (edge case), fall through to normal flow. This skips signature verification, ingest, and broadcast for known events.
A2: New _handle_backfill
Same as _handle_publish but without _broadcast_event:
async def _handle_backfill(self, msg: dict) -> list[dict] | None:
    # Same as publish: parse, dedup check, verify, ingest, receipt
    # Key difference: NO _broadcast_event call
    # Duplicates also return a receipt without broadcast
Extract shared logic into a _store_event(event, broadcast: bool) helper to avoid duplication between publish and backfill handlers.
A3: New _handle_sync_ids
async def _handle_sync_ids(self, msg: dict) -> list[dict] | None:
    group = msg.get("group", "")
    if not group:
        return [{"type": "error", "message": "group required"}]
    known_set = await self._store.get_known_set(group)
    ids = sorted(known_set)
    logger.info("sync_ids group=%s... -> %d ids", group[:16], len(ids))
    return [{"type": "ids", "group": group, "ids": ids}]
Uses existing get_known_set() — already implemented in all store backends.
A4: New sync lock state + handlers
Add to __init__:
self._sync_locks: dict[str, tuple[str, float]] = {}  # group → (client_id, expires_at)
_handle_sync_lock:
async def _handle_sync_lock(self, msg: dict) -> list[dict]:
    group = msg.get("group", "")
    client_id = msg.get("client_id", "")
    if not group or not client_id:
        return [{"type": "error", "message": "group and client_id required"}]

    now = time.time()
    TTL = 30
    existing = self._sync_locks.get(group)

    if existing is not None:
        holder, expires_at = existing
        if expires_at > now and holder != client_id:
            # Locked by someone else
            return [{"type": "sync_lock_denied", "group": group,
                     "expires_in": int(expires_at - now)}]

    # Grant or renew (no holder, expired, or same client)
    self._sync_locks[group] = (client_id, now + TTL)
    logger.info("sync_lock group=%s... granted to %s...", group[:16], client_id[:16])
    return [{"type": "sync_lock_granted", "group": group, "ttl": TTL}]
_handle_sync_unlock:
async def _handle_sync_unlock(self, msg: dict) -> list[dict]:
    group = msg.get("group", "")
    client_id = msg.get("client_id", "")
    existing = self._sync_locks.get(group)
    if existing and existing[0] == client_id:
        del self._sync_locks[group]
        logger.info("sync_unlock group=%s... released by %s...", group[:16], client_id[:16])
    return [{"type": "ok", "message": "unlocked"}]
No timers — expiry is lazy (checked on next sync_lock request).
A5: Update _process_message dispatch
Add four new actions:
elif action == "sync_ids":
    return await self._handle_sync_ids(msg)
elif action == "sync_lock":
    return await self._handle_sync_lock(msg)
elif action == "sync_unlock":
    return await self._handle_sync_unlock(msg)
elif action == "backfill":
    return await self._handle_backfill(msg, ws)
Part B: Spec Updates (spec.md)
B1: Update §9.4 Backfill
Current text (line 776-787): says to republish via publish.
New text: Change step 3 to use backfill instead of publish, and add sync lock coordination:
### 9.4 Backfill

When a client notices (via monitor pass or gap detection) that a relay R
is missing event(s):

1. Fetch the missing event(s) from a sibling relay that has them
   (e.g., via `get` or `sync_ids`).
2. Verify each event (Section 3.5).
3. Acquire a sync lock on R (Section 10.4.10) to coordinate with other
   clients that may also be backfilling. If the lock is denied, wait for
   the current holder's lease to expire, then re-check R's attestation.
   If R has converged, no backfill is needed.
4. Republish the missing events to R via `backfill` (Section 10.4.12),
   NOT `publish`. The `backfill` action stores the event without
   broadcasting it to subscribers, since all subscribers either already
   have the event or will obtain it via their own sync.
5. Release the sync lock (Section 10.4.11).
6. R either:
   - Stores and integrates the events (its next attestation converges), or
   - Refuses to integrate (caught on the next monitor pass; recorded
     as a fault).

Backfill is performed by any client that notices a gap. The network
drifts toward completeness over time without requiring dedicated monitor
infrastructure.
B2: Update §10.3 Relay Validation
Add a note about dedup and the backfill action. After the existing numbered list (step 7), add:
Relays SHOULD check whether an event is already stored (via `has_event`
or equivalent) before performing the expensive signature verification
(step 4). If the event is already stored, the relay returns a receipt
without re-verifying, re-storing, or broadcasting. This optimisation is
important for backfill scenarios where many clients may republish the
same events to a recovering relay.
Update step 7 to clarify the receipt/no-broadcast distinction:
7. Return a receipt to the publishing client. For `publish` actions,
   also broadcast the event to subscribed clients (Section 10.4.2). For
   `backfill` actions, do NOT broadcast (Section 10.4.12).
B3: New §10.4.9 Sync IDs
Insert after §10.4.8 (line 1058), before §10.5:
#### 10.4.9 Sync IDs (Bulk ID Fetch)

Client → Relay:
```json
{"action": "sync_ids", "group": "<group pubkey hex>"}
Relay → Client:
{"type": "ids", "group": "<group pubkey hex>", "ids": ["<event id hex>", ...]}
Returns all event IDs the relay has stored for the group, without the
full event bodies. Clients use this to compute a set difference against
their local known set, then fetch only the missing full events via get
(Section 10.4.3) and backfill only the events the relay is missing via
backfill (Section 10.4.12).
This is an optimisation over sync (Section 10.4.4): for a group with
N events, sync_ids transfers ~64N bytes (just hex IDs) instead of
full event objects. The client can determine exactly which events to
request and which to republish.
If the relay does not host the group, returns:
{"type": "error", "message": "group not hosted"}

### B4: New §10.4.10 Sync Lock

10.4.10 Sync Lock
A sync lock coordinates backfill so that multiple clients discovering
the same relay divergence do not all backfill simultaneously (the
thundering herd problem). The lock is per-group and lease-based.
Client → Relay:
{"action": "sync_lock", "group": "<group pubkey hex>", "client_id": "<user pubkey hex>"}
Relay → Client (granted):
{"type": "sync_lock_granted", "group": "<group pubkey hex>", "ttl": 30}
Relay → Client (denied — another client holds the lock):
{"type": "sync_lock_denied", "group": "<group pubkey hex>", "expires_in": 15}
Rules:
- The lock is per-group. Backfilling group A does not block group B.
- client_id is the client's user pubkey (unique per user).
- TTL is 30 seconds. The holder SHOULD renew by re-sending sync_lock
before the lease expires (suggested: renew at 60% of TTL).
- If the same client_id re-requests the lock, it is renewed (lease
extended). This is the renewal mechanism.
- Expiry is lazy: the relay does not set timers. The lock is considered
expired if now > expires_at when the next sync_lock request
arrives. A crashed client's lock is claimed by the next requester.
- The lock is advisory. Clients that do not support it fall back to
uncoordinated backfill. Relay-side dedup (Section 10.3) makes
uncoordinated backfill safe but less efficient.
- The lock does not affect publish, subscribe, sync, get, or
any other action. It only coordinates backfill.

### B5: New §10.4.11 Sync Unlock

10.4.11 Sync Unlock
Client → Relay:
{"action": "sync_unlock", "group": "<group pubkey hex>", "client_id": "<user pubkey hex>"}
Relay → Client:
{"type": "ok", "message": "unlocked"}
Releases the sync lock for the group if the client_id matches the
current holder. If the client_id does not match (or no lock exists),
the relay returns ok without error — unlock is idempotent.
Clients MUST release the lock when backfill is complete. If a client
disconnects without releasing, the lease expires lazily (Section 10.4.10).

### B6: New §10.4.12 Backfill

10.4.12 Backfill
Client → Relay:
{"action": "backfill", "event": { ... full event object ... }}
Relay → Client:
{"type": "receipt", "receipt": { ... receipt object ... }}
or
{"type": "error", "message": "<human-readable reason>"}
backfill is identical to publish (Section 10.4.2) except:
- The relay performs the same validation, dedup, and storage.
- The relay returns a receipt.
- The relay does NOT broadcast the event to subscribed clients.
backfill is used when a client is healing a relay that is missing
events (Section 9.4). Since all subscribed clients either already have
the event or will obtain it via their own sync, broadcasting would send
redundant events to every subscriber. publish is for newly created
events that subscribers have not yet seen; backfill is for historical
events that subscribers already have.
Clients SHOULD use backfill (not publish) when republishing events
to a relay during healing. Using publish for healing is not an error
(the relay accepts it), but it causes unnecessary broadcasts.

### B7: Update §10.8 Relay Storage Requirements

Add to the MUST list:
- Accept backfill actions and store events without broadcasting.

Add to the SHOULD list (new):
- Check has_event before expensive verification on publish and backfill.
- Support sync_ids for efficient set comparison.
- Support sync_lock / sync_unlock for coordinated backfill.

### B8: Update §13.2 Joining a Group

Update step 5 to mention attestation-gated sync:
5. Sync from all canonical relays. Use attestation comparison
(Section 9.2) as a sync gate: request each relay's attestation,
compare its set_hash to the local known set, and only perform a
full sync if the hashes differ. Use sync_ids (Section 10.4.9) for
efficient ID-only comparison.

Update step 8 to mention sync lock:
8. Verify completeness via attestation comparison across all canonical
relays. If attestations diverge, investigate via monitor pass
(Section 9.3) and backfill (Section 9.4), using sync locks
(Section 10.4.10) to coordinate.

### B9: Update §13.4 Receiving (Live)

Update "On receiving a new attestation" to mention sync lock:
On receiving a new attestation:
1. Verify the attestation signature (Section 9.2.4).
2. Compare set_hash to the local known set.
3. If they match, the relay is in sync — no action needed.
4. If they differ, acquire a sync lock (Section 10.4.10) and perform
backfill (Section 9.4). If the lock is denied, wait for the lease
to expire, re-check the attestation, and retry only if still
divergent.
5. Update the local trust ledger.

---

## Part C: Architecture Doc Updates (`architecture.md`)

### C1: Update §9.4 Backfill

Replace the current paragraph (line 383-390) to mention `backfill` action, sync locks, and sync_ids:

9.4 Backfill — The Network Self-Heals
When a client notices a relay is missing events (detected via
attestation divergence or gap detection), it doesn't just flag the
fault — it fetches the missing events from a sibling relay and
republishes them to the lagging relay via the backfill action (which
stores without broadcasting to subscribers). To prevent the thundering
herd problem (many clients backfilling the same relay simultaneously),
clients use a per-group sync lock with a lease. Only the lock holder
backfills; others wait and re-check the attestation. The relay also
deduplicates: if it already has an event, it returns a receipt without
re-verifying or broadcasting.
For efficiency, clients use sync_ids to fetch only event IDs (not full
events) and compute a set difference, then transfer only the missing
events in each direction. Attestation set_hash comparison acts as a
gate: if the hashes match, no sync is needed at all.
The lagging relay either integrates the backfilled events (its next
attestation converges) or refuses (caught as persistently divergent,
flagged in the trust ledger). The network drifts toward completeness
over time — seconds to minutes, depending on how many clients are
active.

### C2: Update §11.5 WebSocket API

Add the four new actions to the action list (after the existing "Request current attestation" entry):

Sync IDs (bulk ID fetch, no event bodies):
{"action": "sync_ids", "group": "<group pubkey>"}
Acquire sync lock (coordinate backfill):
{"action": "sync_lock", "group": "<group pubkey>", "client_id": "<user pubkey>"}
Release sync lock:
{"action": "sync_unlock", "group": "<group pubkey>", "client_id": "<user pubkey>"}
Backfill an event (store without broadcasting):
{"action": "backfill", "event": { ... }}

Add new response types to the response list:
{"type": "ids", "group": "...", "ids": "<event id>", ...}
{"type": "sync_lock_granted", "group": "...", "ttl": 30}
{"type": "sync_lock_denied", "group": "...", "expires_in": 15}

### C3: Update §11.3 Relay Validation

Add a paragraph about dedup:
Relays should check whether an event is already stored before performing
expensive signature verification. If the event ID is already in the
store, the relay returns a receipt without re-verifying, re-storing, or
broadcasting. This is important for backfill scenarios where many
clients may republish the same events to a recovering relay — without
dedup, the relay would re-verify and re-broadcast thousands of
duplicate events.

---

## Part D: Python Architecture Doc Updates (`python-architecture.md`)

### D1: Update §3.7 RelayTransport Protocol

Add new methods to the Protocol:
```python
    async def sync_ids(self, group: str) -> list[str]: ...
    async def sync_lock(self, group: str, client_id: str) -> dict: ...
    async def sync_unlock(self, group: str, client_id: str) -> None: ...
    async def backfill(self, event: Event) -> Receipt: ...
D2: Update §3.7 RelayServer description
Add: "Implements sync_ids (returns event ID list), sync_lock/sync_unlock (per-group lease-based coordination), backfill (store without broadcast). Deduplicates publish/backfill via has_event check before verification."
Part E: Implementation Notes Updates (implementation-notes.md)
E1: Update §9.1 Key Constants
Add:
- Sync lock TTL: 30 seconds (renew at ~18s)
- Sync lock renewal interval: 60% of TTL
- Backfill batch size: 10 concurrent publishes
E2: Update §9.4 WebSocket Actions
Add to the action table:
| `sync_ids` | Bulk-fetch event IDs only (no event bodies) |
| `sync_lock` | Acquire/renew per-group backfill coordination lock |
| `sync_unlock` | Release sync lock |
| `backfill` | Store an event without broadcasting to subscribers |
E3: Add new section §3.20 (Critical Gotchas)
### 3.20 Backfill vs Publish

`backfill` stores an event without broadcasting. `publish` stores AND
broadcasts. Use `backfill` when healing a relay (the events are
historical, subscribers already have them). Use `publish` for new
events. The relay deduplicates both: if it already has the event, it
returns a receipt without re-verifying or broadcasting.

### 3.21 Sync lock is advisory and lease-based

The sync lock prevents thundering herd during backfill. It is per-group,
lease-based (30s TTL, lazy expiry — no timers on the relay), and
advisory (clients that don't support it fall back to uncoordinated
backfill, which is safe due to relay-side dedup). Renew at 60% of TTL
during long backfills. Release explicitly when done.
Part F: Bracken Changes
F1: New file bracken/src/fern/completeness.ts
import { sha256Hex, isValidPubkey, isValidSig } from './utils'
import { verifySignature } from './crypto'
import type { Attestation } from './relay'

export const EMPTY_SET_HASH = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'

export async function computeSetHash(ids: Set<string>): Promise<string> {
  if (ids.size === 0) return EMPTY_SET_HASH
  const sorted = [...ids].sort()
  const joined = sorted.join('\n')
  return sha256Hex(new TextEncoder().encode(joined))
}

export function canonicalSerializationAttestation(att: Attestation): Uint8Array {
  const arr = [
    att.group,
    att.relay,
    att.set_hash,
    [...att.tips].sort(),
    att.count,
    att.prev,
    att.ts,
  ]
  return new TextEncoder().encode(JSON.stringify(arr))
}

export function verifyAttestation(att: Attestation): boolean {
  if (!isValidPubkey(att.group)) return false
  if (!isValidPubkey(att.relay)) return false
  if (!isValidSig(att.sig)) return false
  if (!Number.isInteger(att.ts) || att.ts <= 0) return false
  if (!Number.isInteger(att.count) || att.count < 0) return false
  if (att.prev !== null) {
    if (!/^[0-9a-f]{64}$/.test(att.prev)) return false
  }
  const tipsSorted = [...att.tips].sort()
  if (JSON.stringify(att.tips) !== JSON.stringify(tipsSorted)) return false
  const canon = canonicalSerializationAttestation(att)
  return verifySignature(att.relay, canon, att.sig)
}

export async function hashAttestation(att: Attestation): Promise<string> {
  return sha256Hex(canonicalSerializationAttestation(att))
}
F2: bracken/src/fern/relay.ts — new methods
Add to RelayClient:
async syncIds(group: string): Promise<string[]> {
  const msg = await this.sendRequest<{ ids: string[] }>('ids', 'sync_ids', { group })
  return msg.ids ?? []
}

async syncLock(group: string, clientId: string): Promise<{ granted: boolean; ttl?: number; expiresIn?: number }> {
  try {
    const msg = await this.sendRequest<{ ttl?: number; expires_in?: number }>(
      'sync_lock_granted', 'sync_lock', { group, client_id: clientId }
    )
    return { granted: true, ttl: msg.ttl }
  } catch (e) {
    // sync_lock_denied comes as a non-error message type — need to handle
    // Actually: sendRequest rejects on type==='error', but sync_lock_denied
    // is not 'error' type. Need special handling.
    // ...handle denied response
  }
}

async syncUnlock(group: string, clientId: string): Promise<void> {
  await this.sendRequest('ok', 'sync_unlock', { group, client_id: clientId })
}

async backfill(event: FernEvent): Promise<Receipt> {
  const msg = await this.sendRequest<{ receipt: Receipt }>('receipt', 'backfill', { event })
  return msg.receipt
}
Note on syncLock: The sendRequest method currently matches by expected response type. sync_lock_granted and sync_lock_denied are two different response types. Need to handle this — either register both as resolvers, or add a variant of sendRequest that accepts multiple expected types. The cleanest approach: register the resolver under both sync_lock_granted and sync_lock_denied, and check msg.type in the resolver.
F3: bracken/src/fern/db.ts — group-specific ID lookup
export async function getGroupEventIds(group: string): Promise<Set<string>> {
  const d = await getDB()
  const keys = await d.getAllKeysFromIndex('events', 'by-group', group)
  return new Set(keys as string[])
}
F4: bracken/src/hooks/useBracken.ts — rewrite sync flow
Bracken runtime behavior:
- Bracken should not create sleep-based background healer timers. Relay
  attestations are already pushed periodically, so future attestation pushes are
  the retry trigger.
- Track at most one in-flight heal task per `(relayUrl, groupPubkey)`. New
  attestation pushes should reuse or ignore the existing task instead of
  creating duplicate sync/backfill work.
- If `sync_lock` is denied, record `nextRetryAt = now + expires_in` for
  `(relayUrl, groupPubkey)` and stop this pass.
- On a later attestation push, reconnect, manual sync, or group activation, if
  `now >= nextRetryAt`, re-check the attestation. If the relay has converged,
  clear the retry gate. If it is still divergent, try to acquire the lock and
  continue healing.
- Clear in-flight task and retry-gate state when the relay disconnects, the user
  switches groups, the user logs out, or the component unmounts.
- This keeps abandoned-lock recovery event-driven rather than timer-driven: if
  the first healing client closes its tab or loses network, another Bracken
  client can take over on the next attestation/sync trigger after the lease
  expires.

New syncDiff function (inside the hook, using useCallback):
const syncDiff = useCallback(async (
  client: RelayClient,
  groupPubkey: string,
  localIds: Set<string>,
  identityPubkey: string,
): Promise<{ fetched: number; backfilled: number }> => {
  // 1. Request attestation
  let att: Attestation
  try {
    att = await client.requestAttestation(groupPubkey)
  } catch {
    // Fallback: full sync
    return fallbackFullSync(client, groupPubkey)
  }

  // 2. Verify attestation signature
  if (!verifyAttestation(att)) {
    console.error('attestation verification failed for', client.url)
    return fallbackFullSync(client, groupPubkey)
  }

  // 3. Compare set_hash
  const localHash = await computeSetHash(localIds)
  if (att.set_hash === localHash) {
    return { fetched: 0, backfilled: 0 }  // In sync — zero transfer
  }

  // 4. Acquire sync lock
  let lockResult
  try {
    lockResult = await client.syncLock(groupPubkey, identityPubkey)
  } catch {
    // Relay doesn't support sync_lock — proceed without coordination
    lockResult = { granted: true }
  }

  if (!lockResult.granted) {
    // Record nextRetryAt for this relay/group and stop this pass. A future
    // attestation or sync trigger will retry after the lease window.
    setHealRetryGate(client.url, groupPubkey, Date.now() + (lockResult.expiresIn ?? 30) * 1000)
    return { fetched: 0, backfilled: 0 }
  }

  // 5. Set up lock renewal timer
  let renewTimer: ReturnType<typeof setInterval> | null = null
  if (lockResult.ttl) {
    renewTimer = setInterval(async () => {
      try { await client.syncLock(groupPubkey, identityPubkey) } catch {}
    }, lockResult.ttl * 600)  // 60% of TTL
  }

  try {
    // 6. Sync IDs and diff
    let relayIds: Set<string>
    try {
      relayIds = new Set(await client.syncIds(groupPubkey))
    } catch {
      // Fallback: full sync
      return fallbackFullSync(client, groupPubkey)
    }

    const missingLocally = [...relayIds].filter(id => !localIds.has(id))
    const missingOnRelay = [...localIds].filter(id => !relayIds.has(id))

    // 7. Fetch missing events (get)
    for (const id of missingLocally) {
      try {
        const event = await client.get(id)
        if (event) {
          await verifyEvent(event)
          await putEvent(event)
        }
      } catch (e) {
        console.error('syncDiff get failed for', id.slice(0, 16), e)
      }
    }

    // 8. Backfill missing events (batched, 10 concurrent)
    const localEvents = await getGroupEvents(groupPubkey)
    const toBackfill = localEvents.filter(e => missingOnRelay.includes(e.id))
    await batchBackfill(toBackfill, client)

    return { fetched: missingLocally.length, backfilled: missingOnRelay.length }
  } finally {
    // 9. Release lock
    if (renewTimer) clearInterval(renewTimer)
    try { await client.syncUnlock(groupPubkey, identityPubkey) } catch {}
    clearHealRetryGate(client.url, groupPubkey)
  }
}, [])
batchBackfill helper:
async function batchBackfill(events: FernEvent[], client: RelayClient, batchSize = 10): Promise<void> {
  for (let i = 0; i < events.length; i += batchSize) {
    const batch = events.slice(i, i + batchSize)
    await Promise.all(batch.map(e => client.backfill(e).catch(() => null)))
  }
}
fallbackFullSync helper (backward compatibility with relays that don't support new actions):
async function fallbackFullSync(client: RelayClient, groupPubkey: string): Promise<{fetched: number, backfilled: number}> {
  const syncEvents = await client.sync(groupPubkey)
  for (const event of syncEvents) {
    try { await verifyEvent(event); await putEvent(event) } catch (e) { console.error(e) }
  }
  return { fetched: syncEvents.length, backfilled: 0 }
}
Rewrite setupRelay — replace the current sync block (lines 211-227) with:
await client.subscribe(groupPubkey)

// Attestation-gated sync-diff
const localIds = await getGroupEventIds(groupPubkey)
const result = await syncDiff(client, groupPubkey, localIds, identity?.publicKey ?? '')
if (result.fetched > 0) {
  const updated = await getGroupEvents(groupPubkey)
  setEvents(updated)
  const { state: derived } = deriveGroupState(updated)
  setState(derived)
}
Attestation push handler — register in setupRelay after client.onEvent(...):
client.onAttestation(async (att) => {
  if (att.group !== groupPubkey) return
  if (!verifyAttestation(att)) {
    console.error('attestation push verification failed for', client.url)
    return
  }
  const localIds = await getGroupEventIds(groupPubkey)
  const localHash = await computeSetHash(localIds)
  if (att.set_hash !== localHash) {
    if (!canRetryHeal(client.url, groupPubkey)) return
    // Divergence detected — trigger sync-diff
    const result = await syncDiff(client, groupPubkey, localIds, identity?.publicKey ?? '')
    if (result.fetched > 0 || result.backfilled > 0) {
      const updated = await getGroupEvents(groupPubkey)
      setEvents(updated)
      const { state: derived } = deriveGroupState(updated)
      setState(derived)
    }
  }
})
Part G: Python Client + CLI Changes

Goal: make the CLI use the same efficient healing path as Bracken, while keeping
backward compatibility with older relays that only support `sync` and `publish`.

G1: Update `src/fern/transport/interfaces.py`

Add these methods to `RelayTransport`:
```python
async def sync_ids(self, group: str) -> list[str]: ...
async def sync_lock(self, group: str, client_id: str) -> SyncLockResult: ...
async def sync_unlock(self, group: str, client_id: str) -> None: ...
async def backfill(self, event: Event) -> Receipt: ...
```

Define a small frozen dataclass near the interface:
```python
@dataclass(frozen=True)
class SyncLockResult:
    granted: bool
    ttl: int | None = None
    expires_in: int | None = None
```

Use a typed result instead of a raw `dict`, because CLI orchestration will branch
on `granted`, `ttl`, and `expires_in`.

G2: Update `src/fern/transport/websocket_client.py`

Add request/response methods:
- `sync_ids(group)` sends `{"action": "sync_ids", "group": group}` and expects
  `{"type": "ids", "ids": [...]}`.
- `sync_lock(group, client_id)` sends `sync_lock` and accepts either
  `sync_lock_granted` or `sync_lock_denied`.
- `sync_unlock(group, client_id)` sends `sync_unlock` and expects `ok`.
- `backfill(event)` sends `backfill` and expects a `receipt`.

Important implementation detail: the current single-reader model routes
request responses through `_response_queue`. `sync_lock` must accept two
non-error response types. Do not add a second reader task or a raw websocket
listener. Add a small helper that waits for any response type in a set, or
handle both response types inside `sync_lock`'s response loop.

G3: Update `src/fern/transport/fake.py`

Add the same methods to `FakeRelay` so integration tests can exercise the new
client flow without opening a network socket:
- `sync_ids(group)` returns `sorted(await store.get_known_set(group))`.
- `sync_lock(...)` grants immediately, or optionally models the same per-group
  lease semantics as the real relay.
- `sync_unlock(...)` releases the fake lock if held.
- `backfill(event)` stores and receipts without invoking event callbacks.

Keep `publish(event)` behavior unchanged: publish still stores, receipts, and
notifies callbacks.

G4: Add reusable Python sync/healing helper

Create a reusable helper rather than wiring this independently into every CLI
command. Suggested file:
`src/fern/client/sync.py`

Suggested API:
```python
@dataclass(frozen=True)
class SyncDiffResult:
    fetched: int = 0
    backfilled: int = 0
    used_fallback: bool = False

async def sync_diff(
    *,
    transport: RelayTransport,
    group: str,
    store: EventStore,
    client_id: str,
    batch_size: int = 10,
    wait_on_lock: bool = False,
) -> SyncDiffResult: ...
```

Algorithm:
1. Request and verify the relay attestation.
2. Compute the local known-set and `compute_set_hash(local_ids)`.
3. If hashes match, return zero work.
4. Acquire `sync_lock`.
   - If granted, continue.
   - If denied and `wait_on_lock=False`, skip this relay for this pass. This is
     the CLI policy: heal relays available right now, but do not block a
     one-shot command waiting for another client.
   - If denied and `wait_on_lock=True`, wait `expires_in`, re-request
     attestation, and return if converged. Retry once; if still denied, skip
     this relay for this pass. This is available for non-React long-lived
     sessions that explicitly want blocking lease waits.
5. Call `sync_ids` and compute:
   - `missing_locally = relay_ids - local_ids`
   - `missing_on_relay = local_ids - relay_ids`
6. Fetch `missing_locally` via `get`, verify each event, and store it.
7. Backfill `missing_on_relay` from the local store using `backfill`, batching
   with a concurrency limit of 10.
8. Release `sync_unlock` in a `finally` block.

Fallback behavior:
- If `attestation`, `sync_ids`, `sync_lock`, or `backfill` is unsupported by
  the relay, fall back to the current full `sync` behavior for fetching.
- If `backfill` is unsupported but the relay supports `publish`, use `publish`
  for historical backfill only as a compatibility fallback.
- Mark `used_fallback=True` so CLI commands can expose this in verbose output
  later without changing the core algorithm.

Lock policy by caller:
- CLI helpers MUST call `sync_diff(..., wait_on_lock=False)`. A CLI command
  should not sleep for a 30-second lease just because another client is already
  healing a relay. It should update from and backfill any relays it can lock
  immediately, skip locked relays, and exit.
- Long-lived clients MAY call `sync_diff(..., wait_on_lock=True)` if they
  explicitly want blocking lease waits. Bracken should not do that; it should
  use the event-driven retry gate described in Part F4 so React lifecycle
  behavior stays simple and testable.

G5: Update bootstrap/session helpers

Update `src/fern/client/bootstrap.py`:
- `initial_sync()` should use `sync_diff()` per transport when a `client_id`
  is available.
- Keep a full-sync path for tests and callers that do not have a user identity.

Update `src/fern/client/session.py`:
- `GroupSession.join_group()` should prefer `sync_diff()` after genesis fetch.
- `_handle_attestation()` can use `sync_diff()` for missing-event healing after
  the monitor pass identifies divergence, but avoid recursive attestation loops:
  deduplicate in-flight syncs per `(relay_pubkey, group)`.

G6: Update CLI commands

The CLI commands that currently sync before deriving state should use the new
helper through a shared wrapper, not hand-roll the flow per command.

Add a helper in `cli/config.py` or a new `cli/sync.py`:
```python
async def sync_group_from_transports(
    *,
    group_pubkey: str,
    store: EventStore,
    transports: Sequence[RelayTransport],
    client_id: str,
) -> list[SyncDiffResult]: ...
```

Use it in commands that need current history/state:
- `fern group join`
- `fern group info`
- `fern group members`
- `fern post`
- `fern read`
- `fern watch`
- `fern verify`

Command behavior:
- Default output stays quiet unless there is an error.
- Existing commands still work against old relays because the helper falls back
  to full sync.
- CLI sync is opportunistic: if a relay is already locked by another client,
  skip that relay for this command instead of waiting for the lease. The next
  CLI invocation can re-check it, and long-lived Bracken clients can take over
  lease-expired work in the background.
- `watch` should perform sync-diff before subscribing/rendering, then use
  attestation pushes for ongoing healing if the connected client/session path is
  available.
- `post` should run sync-diff before selecting parents so it uses current
  connected heads and avoids building on stale local state.
- `verify` should report whether each relay was in sync, fetched from, or
  backfilled, alongside existing trust/attestation checks.

G7: Tests for CLI/Python support

Add focused tests:
- WebSocket client/server round-trip for `sync_ids`.
- WebSocket client/server `sync_lock` granted/denied/unlock behavior.
- `backfill` stores and receipts but does not broadcast to subscribers.
- `sync_diff()` fetches events the local store is missing.
- `sync_diff()` backfills events the relay is missing.
- `sync_diff()` falls back to full `sync` against a fake old relay without the
  new methods.
- A CLI-level integration test for `read` or `post` proving the cache is updated
  through sync-diff before state/head selection.

Execution Order
1. Part A (Python relay: dedup + 4 new actions) — standalone, testable independently
2. Part B-E (spec + docs) — can be done in parallel with code
3. Part G1-G3 (Python transport interfaces, WebSocket client, FakeRelay) — needed before CLI orchestration
4. Part G4-G7 (Python sync helper, session/bootstrap wiring, CLI adoption, tests)
5. Part F1 (Bracken completeness.ts) — standalone, testable
6. Part F2-F3 (Bracken relay client + db) — standalone
7. Part F4 (Bracken sync flow rewrite) — depends on F1-F3
What's NOT included (explicitly deferred)
- prev chain verification for attestations (we verify signatures but don't check chain continuity)
- Fraud proofs (receipt matching, trust ledger, fault recording)
- Gap healing (fetching missing parent events from sibling relays via get())
