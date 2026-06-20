import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import type { FernEvent, EventInput } from '../fern/events'
import { buildEvent, verifyEvent } from '../fern/events'
import type { Keypair } from '../fern/crypto'
import { generateKeypair, keypairFromSeed } from '../fern/crypto'
import type { GroupState } from '../fern/state'
import { deriveGroupState } from '../fern/state'
import {
  getIdentity, saveIdentity, putEvent, getGroupEvents,
  getTips, putReceipt, putRelayPin, setMeta, getMeta,
  clearLocalData, getGroupEventIds,
} from '../fern/db'
import { RelayClient, parseGroupAddress } from '../fern/relay'
import type { Attestation, Receipt } from '../fern/relay'
import { computeSetHash, verifyAttestation } from '../fern/completeness'

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
    const receipt: Receipt = await client.publish(event)
    await putReceipt({
      event_id: receipt.event_id,
      group: receipt.group,
      relay: receipt.relay,
      ts: receipt.ts,
      sig: receipt.sig,
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
): Promise<{ fetched: number; backfilled: number }> {
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
  return { fetched, backfilled: 0 }
}

async function batchBackfill(
  events: FernEvent[],
  client: RelayClient,
  batchSize = 10,
): Promise<number> {
  let backfilled = 0
  for (let i = 0; i < events.length; i += batchSize) {
    const batch = events.slice(i, i + batchSize)
    const results = await Promise.all(batch.map(async (event) => {
      try {
        await client.backfill(event)
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
    backfilled += results.filter(Boolean).length
  }
  return backfilled
}

function sortForBackfill(events: FernEvent[]): FernEvent[] {
  return [...events].sort((a, b) => {
    if (a.type === 'genesis' && b.type !== 'genesis') return -1
    if (a.type !== 'genesis' && b.type === 'genesis') return 1
    return a.ts - b.ts || a.id.localeCompare(b.id)
  })
}

async function syncDiff(
  client: RelayClient,
  groupPubkey: string,
  identityPubkey: string,
  onLockDenied?: (expiresIn: number) => void,
): Promise<{ fetched: number; backfilled: number }> {
  let att: Attestation
  try {
    att = await client.requestAttestation(groupPubkey)
  } catch (e) {
    if (String(e).toLowerCase().includes('group not hosted')) {
      const localEvents = sortForBackfill(await getGroupEvents(groupPubkey))
      return { fetched: 0, backfilled: await batchBackfill(localEvents, client) }
    }
    return fallbackFullSync(client, groupPubkey)
  }

  if (!verifyAttestation(att)) {
    console.error('attestation verification failed for', client.url)
    return fallbackFullSync(client, groupPubkey)
  }

  const localIds = await getGroupEventIds(groupPubkey)
  const localHash = await computeSetHash(localIds)
  if (att.set_hash === localHash) return { fetched: 0, backfilled: 0 }

  try {
    const lock = await client.syncLock(groupPubkey, identityPubkey)
    if (!lock.granted) {
      onLockDenied?.(lock.expiresIn ?? 30)
      return { fetched: 0, backfilled: 0 }
    }
  } catch {
    // Older relays may not support advisory locks. Relay-side dedup keeps
    // uncoordinated backfill safe, though less efficient.
  }

  try {
    let relayIds: Set<string>
    try {
      relayIds = new Set(await client.syncIds(groupPubkey))
    } catch (e) {
      if (String(e).toLowerCase().includes('group not hosted')) {
        const localEvents = sortForBackfill(await getGroupEvents(groupPubkey))
        return { fetched: 0, backfilled: await batchBackfill(localEvents, client) }
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
    const localEvents = sortForBackfill(
      (await getGroupEvents(groupPubkey)).filter((event) => missingSet.has(event.id)),
    )
    const backfilled = await batchBackfill(localEvents, client)
    return { fetched, backfilled }
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
      setGroups(savedGroups)
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
            )
            if (result.fetched > 0 || result.backfilled > 0) {
              healRetryGateRef.current.delete(key)
              await refreshGroup()
            }
          } finally {
            healInFlightRef.current.delete(key)
          }
        }

        client.onClose(() => {
          clientMap.delete(url)
          healInFlightRef.current.delete(healKey(url))
          healRetryGateRef.current.delete(healKey(url))
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
        client.onAttestation(async (att) => {
          if (att.group !== groupPubkey) return
          if (!verifyAttestation(att)) {
            console.error('attestation push verification failed for', client.url)
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
        healInFlightRef.current.delete(healKey(url))
        healRetryGateRef.current.delete(healKey(url))
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
    setMessageDeliveries({})
  }, [])

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

      for (const url of relays) {
        try {
          const client = new RelayClient(url)
          await client.connect()
          await client.publish(joinEvent)
          await client.close()
        } catch {
          // best effort
        }
      }

      setActiveGroup(groupPubkey)
    },
    [identity, groups],
  )

  const sendMessage = useCallback(
    async (text: string, channel = 'general'): Promise<boolean> => {
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

  const modAction = useCallback(
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
      } else if (type === 'chat.channel_create' || type === 'chat.channel_delete') {
        if (!('name' in content)) return
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
      options?: { description?: string; public?: boolean; channels?: string[] },
    ): Promise<{ ok: number; total: number; error?: string }> => {
      if (!identity) throw new Error('No identity')
      const groupKeypair = generateKeypair()
      const channelList = new Set(['general'])
      for (const ch of options?.channels ?? []) {
        channelList.add(ch.trim())
      }
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
          mods: [identity.publicKey],
          relays: relayUrls,
          app: 'chat',
          'chat.channels': [...channelList],
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
    [identity, activeGroup, groups, getPublishParents],
  )

  return {
    identity,
    loading,
    groups,
    activeGroup,
    events,
    state,
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
    modAction,
    setNickname,
  }
}
