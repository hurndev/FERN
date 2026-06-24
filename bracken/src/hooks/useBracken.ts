import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import type { FernEvent, EventInput } from '../fern/events'
import { buildEvent, verifyEvent } from '../fern/events'
import type { Keypair } from '../fern/crypto'
import { generateKeypair, keypairFromSeed } from '../fern/crypto'
import type { GroupState } from '../fern/state'
import { deriveGroupState } from '../fern/state'
import {
  getIdentity, saveIdentity, putEvent, getGroupEvents,
  getTips, putEventReceipt, putRelayPin, setMeta, getMeta,
  clearLocalData, getGroupEventIds,
} from '../fern/db'
import { RelayClient, parseGroupAddress } from '../fern/relay'
import type { GroupStatus, EventReceipt } from '../fern/relay'
import { randomHexId } from '../fern/utils'
import { computeSetHash, verifyGroupStatus } from '../fern/completeness'
import type {
  HealChallenge,
  GroupHostAttestation,
  InventoryAttestation,
  HealBatchResult,
} from '../fern/heal_attestations'
import {
  computeChallengeId,
  verifyHealChallenge,
  verifyGroupHostAttestation,
  verifyInventoryAttestation,
} from '../fern/heal_attestations'

export interface GroupEntry {
  pubkey: string
  name: string
  relays: string[]
}

export interface RelayConnection {
  url: string
  client?: RelayClient
  connected: boolean
  pubkey: string
}

export interface MessageDelivery {
  state: 'sending' | 'failed'
  ok: number
  total: number
  error?: string
}

async function publishToRelaysWith(
  event: FernEvent,
  client: RelayClient,
): Promise<boolean> {
  try {
    const event_receipt: EventReceipt = await client.publish(event)
    await putEventReceipt({
      event_id: event_receipt.event_id,
      group: event_receipt.group,
      relay: event_receipt.relay,
      ts: event_receipt.ts,
      sig: event_receipt.sig,
    })
    return true
  } catch {
    return false
  }
}

async function publishToRelaysEphemeral(
  event: FernEvent,
  url: string,
): Promise<boolean> {
  const client = new RelayClient(url)
  try {
    await client.connect()
    return await publishToRelaysWith(event, client)
  } catch {
    return false
  } finally {
    await client.close()
  }
}

async function fallbackFullSync(
  client: RelayClient,
  groupPubkey: string,
): Promise<{ fetched: number; healed: number }> {
  const syncEvents = await client.sync(groupPubkey)
  let fetched = 0
  for (const event of syncEvents) {
    try {
      await verifyEvent(event)
      await putEvent(event)
      fetched += 1
    } catch (e) {
      console.error('fallback sync verifyEvent failed for', event.type, event.id?.slice(0, 16), e)
    }
  }
  return { fetched, healed: 0 }
}

async function batchHeal(
  events: FernEvent[],
  client: RelayClient,
  batchSize = 10,
): Promise<number> {
  let healed = 0
  for (let i = 0; i < events.length; i += batchSize) {
    const batch = events.slice(i, i + batchSize)
    const results = await Promise.all(batch.map(async (event) => {
      try {
        await client.heal(event)
        return true
      } catch {
        try {
          await client.publish(event)
          return true
        } catch {
          return false
        }
      }
    }))
    healed += results.filter(Boolean).length
  }
  return healed
}

function sortForHeal(events: FernEvent[]): FernEvent[] {
  return [...events].sort((a, b) => {
    if (a.type === 'genesis' && b.type !== 'genesis') return -1
    if (a.type !== 'genesis' && b.type === 'genesis') return 1
    return a.ts - b.ts || a.id.localeCompare(b.id)
  })
}

async function attemptTrustedHeal(
  laggingClient: RelayClient,
  groupPubkey: string,
  events: FernEvent[],
  relays?: Map<string, RelayClient>,
): Promise<{ healed: number; failedIds: string[] } | null> {
  if (events.length === 0) return { healed: 0, failedIds: [] }

  const BATCH_LIMIT = 500
  let challenge: HealChallenge
  try {
    const ids = events.map((e) => e.id)
    challenge = await laggingClient.getHealChallenge(groupPubkey, ids)
    const now = Math.floor(Date.now() / 1000)
    if (!(await verifyHealChallenge(challenge, undefined, now))) {
      console.warn('trusted-heal: challenge verification failed')
      return null
    }
  } catch (e) {
    console.warn('trusted-heal: getHealChallenge failed, falling back to slow heal', e)
    return null
  }

  const hostAtts: GroupHostAttestation[] = []
  const invAtts: { attestation: InventoryAttestation; ids: string[] }[] = []
  const tempClients: RelayClient[] = []

  try {
    const challengeId = await computeChallengeId(challenge)

    for (const witness of challenge.trusted_witnesses) {
      let witnessClient: RelayClient | undefined
      if (relays) {
        for (const c of relays.values()) {
          if (c.relayPubkey === witness.relay && c.isConnected) {
            witnessClient = c
            break
          }
        }
      }

      if (!witnessClient) {
        try {
          witnessClient = new RelayClient(witness.url)
          await witnessClient.connect()
          const meta = await witnessClient.fetchMetadata()
          if (meta.pubkey !== witness.relay) {
            console.warn(`trusted-heal: witness ${witness.relay.slice(0, 12)}… pubkey mismatch`)
            await witnessClient.close()
            continue
          }
          tempClients.push(witnessClient)
        } catch {
          console.warn(`trusted-heal: failed to connect to witness ${witness.url}`)
          if (witnessClient) await witnessClient.close()
          continue
        }
      }

      try {
        const hostAtt = await witnessClient.getGroupHostAttestation(challenge)
        const now = Math.floor(Date.now() / 1000)
        if (!(await verifyGroupHostAttestation(hostAtt, challengeId, witness.relay, now))) {
          console.warn('trusted-heal: host attestation verification failed for', witness.relay.slice(0, 12))
          continue
        }
        if (!hostAtt.hosts) {
          console.warn('trusted-heal: witness does not host group', witness.relay.slice(0, 12))
          continue
        }
        hostAtts.push(hostAtt)
      } catch (e) {
        console.warn('trusted-heal: getGroupHostAttestation failed for', witness.relay.slice(0, 12), e)
        continue
      }

      try {
        const ids = events.map((ev) => ev.id)
        const invResult = await witnessClient.getInventoryAttestation(challenge, ids)
        if (invResult.inventoryMissing) {
          console.warn('trusted-heal: witness missing all events', witness.relay.slice(0, 12))
          continue
        }
        const att = invResult.attestation
        if (!att) continue
        const now = Math.floor(Date.now() / 1000)
        if (!(await verifyInventoryAttestation(att, challengeId, witness.relay, now, invResult.covered))) {
          console.warn('trusted-heal: inventory attestation verification failed for', witness.relay.slice(0, 12))
          continue
        }
        invAtts.push({ attestation: att, ids: invResult.covered })
      } catch (e) {
        console.warn('trusted-heal: getInventoryAttestation failed for', witness.relay.slice(0, 12), e)
      }
    }

    if (hostAtts.length === 0 || invAtts.length === 0) {
      console.warn('trusted-heal: no valid attestations collected')
      return null
    }

    let totalHealed = 0
    const allFailedIds: string[] = []

    for (let i = 0; i < events.length; i += BATCH_LIMIT) {
      const batch = events.slice(i, i + BATCH_LIMIT)
      const batchIds = batch.map((e) => e.id)

      const relevantInvAtts = invAtts
        .map((ia) => ({
          attestation: ia.attestation,
          ids: ia.ids.filter((id) => batchIds.includes(id)),
        }))
        .filter((ia) => ia.ids.length > 0)

      if (relevantInvAtts.length === 0) {
        allFailedIds.push(...batchIds)
        continue
      }

      let result: HealBatchResult
      try {
        result = await laggingClient.healBatch(challenge, batch, hostAtts, relevantInvAtts)
      } catch (e) {
        console.warn('trusted-heal: healBatch failed', e)
        allFailedIds.push(...batchIds)
        continue
      }

      totalHealed += result.stored.length
      for (const rej of result.rejected) {
        if (rej.reason === 'insufficient_trusted_witnesses') {
          allFailedIds.push(rej.id)
        }
      }
    }

    return { healed: totalHealed, failedIds: allFailedIds }
  } catch (e) {
    console.warn('trusted-heal: unexpected error, falling back to slow heal', e)
    return null
  } finally {
    for (const tc of tempClients) {
      try {
        await tc.close()
      } catch {
        // best effort
      }
    }
  }
}

async function syncDiff(
  client: RelayClient,
  groupPubkey: string,
  identityPubkey: string,
  onLockDenied?: (expiresIn: number) => void,
  relays?: Map<string, RelayClient>,
): Promise<{ fetched: number; healed: number }> {
  let att: GroupStatus
  try {
    att = await client.requestGroupStatus(groupPubkey)
  } catch (e) {
    if (String(e).toLowerCase().includes('group not hosted')) {
      const localEvents = sortForHeal(await getGroupEvents(groupPubkey))
      return { fetched: 0, healed: await batchHeal(localEvents, client) }
    }
    return fallbackFullSync(client, groupPubkey)
  }

  if (!verifyGroupStatus(att)) {
    console.error('group_status verification failed for', client.url)
    return fallbackFullSync(client, groupPubkey)
  }

  const localIds = await getGroupEventIds(groupPubkey)
  const localHash = await computeSetHash(localIds)
  if (att.set_hash === localHash) return { fetched: 0, healed: 0 }

  try {
    const lock = await client.syncLock(groupPubkey, identityPubkey)
    if (!lock.granted) {
      onLockDenied?.(lock.expiresIn ?? 30)
      return { fetched: 0, healed: 0 }
    }
  } catch {
    // Older relays may not support advisory locks. Relay-side dedup keeps
    // uncoordinated heal safe, though less efficient.
  }

  try {
    let relayIds: Set<string>
    try {
      relayIds = new Set(await client.syncIds(groupPubkey))
    } catch (e) {
      if (String(e).toLowerCase().includes('group not hosted')) {
        const localEvents = sortForHeal(await getGroupEvents(groupPubkey))
        return { fetched: 0, healed: await batchHeal(localEvents, client) }
      }
      return fallbackFullSync(client, groupPubkey)
    }

    const latestLocalIds = await getGroupEventIds(groupPubkey)
    const missingLocally = [...relayIds].filter((id) => !latestLocalIds.has(id))
    const missingOnRelay = [...latestLocalIds].filter((id) => !relayIds.has(id))

    let fetched = 0
    for (const id of missingLocally) {
      try {
        const event = await client.get(id)
        if (event) {
          await verifyEvent(event)
          await putEvent(event)
          fetched += 1
        }
      } catch (e) {
        console.error('syncDiff get failed for', id.slice(0, 16), e)
      }
    }

    const missingSet = new Set(missingOnRelay)
    const localEvents = sortForHeal(
      (await getGroupEvents(groupPubkey)).filter((event) => missingSet.has(event.id)),
    )

    let healed = 0
    const BATCH_LIMIT = 500

    const trustedHealResult = await attemptTrustedHeal(
      client,
      groupPubkey,
      localEvents,
      relays,
    )
    if (trustedHealResult !== null) {
      healed += trustedHealResult.healed
      if (trustedHealResult.failedIds.length > 0) {
        const failedSet = new Set(trustedHealResult.failedIds)
        const fallbackEvents = localEvents.filter((e) => failedSet.has(e.id))
        healed += await batchHeal(fallbackEvents, client, BATCH_LIMIT)
      }
    } else {
      healed += await batchHeal(localEvents, client, BATCH_LIMIT)
    }

    return { fetched, healed }
  } finally {
    try {
      await client.syncUnlock(groupPubkey, identityPubkey)
    } catch {
      // Best effort; the lease expires lazily if unlock fails.
    }
  }
}

export function useBracken() {
  const [identity, setIdentity] = useState<Keypair | null>(null)
  const [groups, setGroups] = useState<GroupEntry[]>([])
  const [activeGroup, setActiveGroup] = useState<string | null>(null)
  const [events, setEvents] = useState<FernEvent[]>([])
  const [state, setState] = useState<GroupState | null>(null)
  const [connStates, setConnStates] = useState<RelayConnection[]>([])
  const [defaultNickname, setDefaultNicknameState] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [messageDeliveries, setMessageDeliveries] = useState<Record<string, MessageDelivery>>({})
  const clientsRef = useRef<Map<string, RelayClient>>(new Map())
  const reconnectRef = useRef<((url: string) => void) | null>(null)
  const healInFlightRef = useRef<Set<string>>(new Set())
  const healRetryGateRef = useRef<Map<string, number>>(new Map())

  // Derive the full relay list from the active group's canonical relays, merged
  // with live connection status. This ensures all canonical relays are always
  // shown (never hidden), even before or after connection attempts.
  const relayConns = useMemo(() => {
    const group = groups.find((g) => g.pubkey === activeGroup)
    const canonical = group?.relays ?? []
    const byUrl = new Map(connStates.map((c) => [c.url, c]))
    return canonical.map(
      (url) => byUrl.get(url) ?? { url, connected: false, pubkey: '' },
    )
  }, [groups, activeGroup, connStates])

  // Load identity on mount
  useEffect(() => {
    ;(async () => {
      const stored = await getIdentity()
      if (stored) {
        setIdentity(keypairFromSeed(stored.seed))
      }
      const savedGroups = (await getMeta<GroupEntry[]>('groups')) ?? []
      const savedDefaultNickname = (await getMeta<string>('defaultNickname')) ?? null
      setGroups(savedGroups)
      setDefaultNicknameState(savedDefaultNickname)
      if (savedGroups.length > 0) {
        setActiveGroup(savedGroups[0].pubkey)
      }
      setLoading(false)
    })()
  }, [])

  // Load events when active group changes
  useEffect(() => {
    let cancelled = false
    ;(async () => {
      if (!activeGroup) {
        await Promise.resolve()
        if (!cancelled) {
          setEvents([])
          setState(null)
        }
        return
      }

      const groupEvents = await getGroupEvents(activeGroup)
      const { state: derived } = deriveGroupState(groupEvents)
      if (!cancelled) {
        setEvents(groupEvents)
        setState(derived)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [activeGroup])

  // Subscribe to active group relays
  useEffect(() => {
    if (!activeGroup || groups.length === 0) return
    const groupPubkey = activeGroup
    const group = groups.find((g) => g.pubkey === groupPubkey)
    if (!group) return

    const clientMap = clientsRef.current
    const healInFlight = healInFlightRef.current
    const healRetryGate = healRetryGateRef.current
    let cancelled = false
    const reconnectTimers = new Map<string, ReturnType<typeof setTimeout>>()
    const healKey = (url: string) => `${url}|${groupPubkey}`

    const upsertConn = (url: string, patch: Partial<RelayConnection>) => {
      if (cancelled) return
      setConnStates((prev) => {
        const idx = prev.findIndex((c) => c.url === url)
        if (idx === -1) {
          return [...prev, { url, connected: false, pubkey: '', ...patch }]
        }
        return prev.map((c, i) => (i === idx ? { ...c, ...patch } : c))
      })
    }

    const scheduleReconnect = (url: string, attempt = 0) => {
      if (cancelled) return
      const existing = reconnectTimers.get(url)
      if (existing) clearTimeout(existing)
      const delay = Math.min(2000 * 2 ** attempt, 30000)
      const timer = setTimeout(() => {
        reconnectTimers.delete(url)
        void setupRelay(url, attempt)
      }, delay)
      reconnectTimers.set(url, timer)
    }

    reconnectRef.current = (url: string) => scheduleReconnect(url, 0)

    async function setupRelay(url: string, attempt = 0) {
      if (cancelled) return
      try {
        const client = new RelayClient(url)
        await client.connect()

        try {
          const meta = await client.fetchMetadata()
          if (meta.pubkey) {
            await putRelayPin(url, meta.pubkey)
          }
          client.relayPubkey = meta.pubkey
        } catch {
          // metadata fetch failed (CORS, relay down, etc.) — continue without it
        }

        if (cancelled) {
          await client.close()
          return
        }

        const refreshGroup = async () => {
          const updated = await getGroupEvents(groupPubkey)
          setEvents(updated)
          const { state: derived } = deriveGroupState(updated)
          setState(derived)
        }

        const canRetryHeal = () => {
          const retryAt = healRetryGateRef.current.get(healKey(url))
          return retryAt === undefined || Date.now() >= retryAt
        }

        const runHeal = async () => {
          const key = healKey(url)
          if (healInFlightRef.current.has(key) || !canRetryHeal()) return
          healInFlightRef.current.add(key)
          try {
            const result = await syncDiff(
              client,
              groupPubkey,
              identity?.publicKey ?? '0'.repeat(64),
              (expiresIn) => {
                healRetryGateRef.current.set(key, Date.now() + expiresIn * 1000)
              },
              clientMap,
            )
            if (result.fetched > 0 || result.healed > 0) {
              healRetryGateRef.current.delete(key)
              await refreshGroup()
            }
          } finally {
            healInFlightRef.current.delete(key)
          }
        }

        client.onClose(() => {
          clientMap.delete(url)
          healInFlight.delete(healKey(url))
          healRetryGate.delete(healKey(url))
          upsertConn(url, { connected: false, client: undefined })
          scheduleReconnect(url)
        })
        client.onEvent(async (event) => {
          try {
            await verifyEvent(event)
            await putEvent(event)
            if (event.group === groupPubkey) {
              await refreshGroup()
            }
          } catch (e) {
            console.error('verifyEvent failed for', event.type, event.id?.slice(0, 16), e)
          }
        })
        client.onGroupStatus(async (att) => {
          if (att.group !== groupPubkey) return
          if (!verifyGroupStatus(att)) {
            console.error('group_status push verification failed for', client.url)
            return
          }
          const localHash = await computeSetHash(await getGroupEventIds(groupPubkey))
          if (att.set_hash !== localHash) {
            void runHeal()
          } else {
            healRetryGateRef.current.delete(healKey(url))
          }
        })
        await client.subscribe(groupPubkey)

        await runHeal()

        clientMap.set(url, client)
        upsertConn(url, { client, connected: true, pubkey: client.relayPubkey })
      } catch {
        upsertConn(url, { connected: false })
        scheduleReconnect(url, attempt + 1)
      }
    }

    for (const url of group.relays) {
      void setupRelay(url)
    }

    return () => {
      cancelled = true
      reconnectRef.current = null
      for (const timer of reconnectTimers.values()) {
        clearTimeout(timer)
      }
      reconnectTimers.clear()
      for (const client of clientMap.values()) {
        client.close()
      }
      for (const url of group.relays) {
        healInFlight.delete(healKey(url))
        healRetryGate.delete(healKey(url))
      }
      clientMap.clear()
      setConnStates([])
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeGroup, groups.map((g) => g.relays.join(',')).join('|')])

  const createIdentity = useCallback(async () => {
    const kp = generateKeypair()
    await saveIdentity({
      pubkey: kp.publicKey,
      seed: kp.seed,
      secretKey: kp.secretKey,
    })
    setIdentity(kp)
  }, [])

  const importIdentity = useCallback(async (seedHex: string) => {
    const kp = keypairFromSeed(seedHex)
    await saveIdentity({
      pubkey: kp.publicKey,
      seed: kp.seed,
      secretKey: kp.secretKey,
    })
    setIdentity(kp)
  }, [])

  const getFailedDeliveryIds = useCallback(() => {
    return new Set(
      Object.entries(messageDeliveries)
        .filter(([, delivery]) => delivery.state === 'failed')
        .map(([eventId]) => eventId),
    )
  }, [messageDeliveries])

  const getPublishParents = useCallback(
    async (groupPubkey: string): Promise<string[]> => {
      const tips = await getTips(groupPubkey, getFailedDeliveryIds())
      if (tips.length > 0) return tips

      const genesis = (await getGroupEvents(groupPubkey)).find((event) => event.type === 'genesis')
      return genesis ? [genesis.id] : []
    },
    [getFailedDeliveryIds],
  )

  const publishToGroupRelays = useCallback(
    async (event: FernEvent, relays: string[]): Promise<{ ok: number; total: number; error?: string }> => {
      if (relays.length === 0) {
        return { ok: 0, total: 0, error: 'No relays configured for this group.' }
      }

      const results = await Promise.all(
        relays.map(async (url) => {
          const existing = clientsRef.current.get(url)
          if (existing && existing.isConnected) {
            const ok = await publishToRelaysWith(event, existing)
            if (!ok) {
              clientsRef.current.delete(url)
              reconnectRef.current?.(url)
            }
            return ok
          }
          const ok = await publishToRelaysEphemeral(event, url)
          if (ok) {
            reconnectRef.current?.(url)
          }
          return ok
        }),
      )

      const ok = results.filter(Boolean).length
      return {
        ok,
        total: relays.length,
        error: ok === relays.length ? undefined : `${ok}/${relays.length} relays accepted the message.`,
      }
    },
    [],
  )

  const logout = useCallback(async () => {
    for (const client of clientsRef.current.values()) {
      await client.close()
    }
    clientsRef.current.clear()
    await clearLocalData()
    setIdentity(null)
    setGroups([])
    setActiveGroup(null)
    setEvents([])
      setState(null)
      setConnStates([])
      setDefaultNicknameState(null)
      setMessageDeliveries({})
  }, [])

  const setDefaultNickname = useCallback(async (name: string | null) => {
    const normalized = name?.trim() || null
    setDefaultNicknameState(normalized)
    await setMeta('defaultNickname', normalized)
  }, [])

  const removeGroupEntry = useCallback(
    async (groupPubkey: string) => {
      const updatedGroups = groups.filter((g) => g.pubkey !== groupPubkey)
      setGroups(updatedGroups)
      await setMeta('groups', updatedGroups)
      if (activeGroup === groupPubkey) {
        const nextActive = updatedGroups[0]?.pubkey ?? null
        setActiveGroup(nextActive)
        if (!nextActive) {
          setEvents([])
          setState(null)
          setConnStates([])
        }
      }
    },
    [activeGroup, groups],
  )

  const leaveGroup = useCallback(
    async (groupPubkey: string): Promise<void> => {
      if (!identity) return
      const group = groups.find((g) => g.pubkey === groupPubkey)
      if (!group) return

      const groupEvents = await getGroupEvents(groupPubkey)
      const { state: derived } = deriveGroupState(groupEvents)
      const isMember = derived?.joined.has(identity.publicKey) ?? false

      if (isMember) {
        const parents = await getPublishParents(groupPubkey)
        if (parents.length > 0) {
          const input: EventInput = {
            type: 'leave',
            group: groupPubkey,
            author: identity.publicKey,
            parents,
            content: {},
            ts: Math.floor(Date.now() / 1000),
            tags: [],
          }
          const event = await buildEvent(input, identity)
          await putEvent(event)
          await publishToGroupRelays(event, group.relays)
        }
      }

      await removeGroupEntry(groupPubkey)
    },
    [identity, groups, getPublishParents, publishToGroupRelays, removeGroupEntry],
  )

  const joinGroup = useCallback(
    async (address: string) => {
      if (!identity) throw new Error('No identity')
      const { groupPubkey, relays } = parseGroupAddress(address)
      if (relays.length === 0) throw new Error('No relay URLs in address')

      // Connect and sync
      for (const url of relays) {
        try {
          const client = new RelayClient(url)
          await client.connect()
          await syncDiff(client, groupPubkey, identity.publicKey)
          await client.close()
          break
        } catch {
          continue
        }
      }

      // Read genesis from local store
      const localEvents = await getGroupEvents(groupPubkey)
      const genesis = localEvents.find((e) => e.type === 'genesis')
      if (!genesis) throw new Error('Could not fetch genesis')

      const groupName = (genesis.content['name'] as string) ?? 'Unnamed'
      const groupEntry: GroupEntry = {
        pubkey: groupPubkey,
        name: groupName,
        relays,
      }
      const updatedGroups = [...groups.filter((g) => g.pubkey !== groupPubkey), groupEntry]
      setGroups(updatedGroups)
      await setMeta('groups', updatedGroups)

      // Publish join event
      const tips = await getTips(groupPubkey)
      const parents = tips.length > 0 ? tips : [genesis.id]
      const joinInput: EventInput = {
        type: 'join',
        group: groupPubkey,
        author: identity.publicKey,
        parents,
        content: {},
        ts: Math.floor(Date.now() / 1000),
        tags: [],
      }
      const joinEvent = await buildEvent(joinInput, identity)
      await putEvent(joinEvent)

      const nickname = defaultNickname?.trim()
      let nicknameEvent: FernEvent | null = null
      if (nickname) {
        const nicknameInput: EventInput = {
          type: 'chat.nickname_set',
          group: groupPubkey,
          author: identity.publicKey,
          parents: [joinEvent.id],
          content: { nickname },
          ts: Math.floor(Date.now() / 1000),
          tags: [],
        }
        nicknameEvent = await buildEvent(nicknameInput, identity)
        await putEvent(nicknameEvent)
      }

      for (const url of relays) {
        try {
          const client = new RelayClient(url)
          await client.connect()
          await client.publish(joinEvent)
          if (nicknameEvent) await client.publish(nicknameEvent)
          await client.close()
        } catch {
          // best effort
        }
      }

      setActiveGroup(groupPubkey)
    },
    [defaultNickname, identity, groups],
  )

  const sendMessage = useCallback(
    async (text: string, channel: string): Promise<boolean> => {
      if (!identity || !activeGroup) return false
      const group = groups.find((g) => g.pubkey === activeGroup)
      if (!group) return false

      const parents = await getPublishParents(activeGroup)
      if (parents.length === 0) return false

      const input: EventInput = {
        type: 'chat.message',
        group: activeGroup,
        author: identity.publicKey,
        parents,
        content: { text, channel },
        ts: Math.floor(Date.now() / 1000),
        tags: [],
      }
      const event = await buildEvent(input, identity)
      await putEvent(event)
      setMessageDeliveries((prev) => ({
        ...prev,
        [event.id]: {
          state: 'sending',
          ok: 0,
          total: group.relays.length,
        },
      }))

      const updated = await getGroupEvents(activeGroup)
      setEvents(updated)
      const { state: derived } = deriveGroupState(updated)
      setState(derived)

      void (async () => {
        const result = await publishToGroupRelays(event, group.relays)
        setMessageDeliveries((prev) => {
          const next = { ...prev }
          if (result.ok >= 1) {
            delete next[event.id]
          } else {
            next[event.id] = {
              state: 'failed',
              ok: result.ok,
              total: result.total,
              error: result.error,
            }
          }
          return next
        })
      })()

      return true
    },
    [identity, activeGroup, groups, getPublishParents, publishToGroupRelays],
  )

  const retryMessage = useCallback(
    async (eventId: string): Promise<void> => {
      const event = events.find((e) => e.id === eventId)
      if (!event) return
      const group = groups.find((g) => g.pubkey === event.group)
      if (!group) return

      setMessageDeliveries((prev) => ({
        ...prev,
        [event.id]: {
          state: 'sending',
          ok: 0,
          total: group.relays.length,
        },
      }))

      const result = await publishToGroupRelays(event, group.relays)
      setMessageDeliveries((prev) => {
        const next = { ...prev }
        if (result.ok >= 1) {
          delete next[event.id]
        } else {
          next[event.id] = {
            state: 'failed',
            ok: result.ok,
            total: result.total,
            error: result.error,
          }
        }
        return next
      })
    },
    [events, groups, publishToGroupRelays],
  )

  const adminAction = useCallback(
    async (type: string, targetPubkey = '', extra?: Record<string, unknown>): Promise<void> => {
      if (!identity || !activeGroup) return
      const group = groups.find((g) => g.pubkey === activeGroup)
      if (!group) return

      const parents = await getPublishParents(activeGroup)
      if (parents.length === 0) return

      const content: Record<string, unknown> = extra ?? {}
      if (type === 'invite') {
        if (!targetPubkey) return
        content['invitee'] = targetPubkey
        content['role'] = 'member'
      } else if (type === 'relay_update') {
        if (!Array.isArray(content['relays'])) return
      } else if (type === 'metadata_update') {
        if (!('name' in content) && !('description' in content)) return
      } else if (type === 'chat.channel_create') {
        if (!('name' in content)) return
      } else if (type === 'chat.channel_update' || type === 'chat.channel_delete') {
        if (!('id' in content)) return
      } else if (type === 'chat.settings_update') {
        if (!('default_channel' in content) && !('system_channel' in content)) return
      } else {
        if (!targetPubkey) return
        content['target'] = targetPubkey
      }

      const input: EventInput = {
        type,
        group: activeGroup,
        author: identity.publicKey,
        parents,
        content,
        ts: Math.floor(Date.now() / 1000),
        tags: [],
      }
      const event = await buildEvent(input, identity)
      await putEvent(event)

      const updated = await getGroupEvents(activeGroup)
      setEvents(updated)
      const { state: derived } = deriveGroupState(updated)
      setState(derived)
      if (type === 'relay_update' && Array.isArray(content['relays'])) {
        const relays = content['relays'].filter((relay): relay is string => typeof relay === 'string')
        const updatedGroups = groups.map((entry) =>
          entry.pubkey === activeGroup ? { ...entry, relays } : entry,
        )
        setGroups(updatedGroups)
        await setMeta('groups', updatedGroups)
      }

      void (async () => {
        await Promise.all(
          group.relays.map(async (url) => {
            try {
              const client = new RelayClient(url)
              await client.connect()
              await client.publish(event)
              await client.close()
            } catch {
              // best effort
            }
          }),
        )
      })()
    },
    [identity, activeGroup, groups, getPublishParents],
  )

  const createGroup = useCallback(
    async (
      name: string,
      relayUrls: string[],
      options?: { description?: string; public?: boolean },
    ): Promise<{ ok: number; total: number; error?: string }> => {
      if (!identity) throw new Error('No identity')
      const groupKeypair = generateKeypair()
      const defaultChannelId = randomHexId()
      const input: EventInput = {
        type: 'genesis',
        group: groupKeypair.publicKey,
        author: identity.publicKey,
        parents: [],
        content: {
          name,
          description: options?.description ?? '',
          public: options?.public ?? true,
          founder: identity.publicKey,
          admins: [identity.publicKey],
          relays: relayUrls,
          app: 'chat',
          'chat.channels': [{
            id: defaultChannelId,
            name: 'general',
            position: 0,
          }],
          'chat.default_channel': defaultChannelId,
          'chat.system_channel': defaultChannelId,
        },
        ts: Math.floor(Date.now() / 1000),
        tags: [],
      }
      const genesis = await buildEvent(input, identity, groupKeypair)
      await putEvent(genesis)

      const result = await publishToGroupRelays(genesis, relayUrls)

      const groupEntry: GroupEntry = {
        pubkey: groupKeypair.publicKey,
        name,
        relays: relayUrls,
      }
      const updatedGroups = [...groups, groupEntry]
      setGroups(updatedGroups)
      await setMeta('groups', updatedGroups)
      setActiveGroup(groupKeypair.publicKey)

      return result
    },
    [identity, groups, publishToGroupRelays],
  )

  const setNickname = useCallback(
    async (name: string): Promise<void> => {
      if (!identity || !activeGroup) return
      await setDefaultNickname(name)
      const group = groups.find((g) => g.pubkey === activeGroup)
      if (!group) return

      const parents = await getPublishParents(activeGroup)
      if (parents.length === 0) return

      const input: EventInput = {
        type: 'chat.nickname_set',
        group: activeGroup,
        author: identity.publicKey,
        parents,
        content: { nickname: name },
        ts: Math.floor(Date.now() / 1000),
        tags: [],
      }
      const event = await buildEvent(input, identity)
      await putEvent(event)

      const updated = await getGroupEvents(activeGroup)
      setEvents(updated)
      const { state: derived } = deriveGroupState(updated)
      setState(derived)

      void (async () => {
        await Promise.all(
          group.relays.map(async (url) => {
            try {
              const client = new RelayClient(url)
              await client.connect()
              await client.publish(event)
              await client.close()
            } catch {
              // best effort
            }
          }),
        )
      })()
    },
    [identity, activeGroup, groups, getPublishParents, setDefaultNickname],
  )

  return {
    identity,
    loading,
    groups,
    activeGroup,
    events,
    state,
    defaultNickname,
    relayConns,
    messageDeliveries,
    setActiveGroup,
    createIdentity,
    importIdentity,
    logout,
    joinGroup,
    sendMessage,
    retryMessage,
    createGroup,
    adminAction,
    setNickname,
    setDefaultNickname,
    leaveGroup,
  }
}
