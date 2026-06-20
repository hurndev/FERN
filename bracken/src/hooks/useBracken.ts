import { useState, useEffect, useCallback, useRef } from 'react'
import type { FernEvent, EventInput } from '../fern/events'
import { buildEvent, verifyEvent } from '../fern/events'
import type { Keypair } from '../fern/crypto'
import { generateKeypair, keypairFromSeed } from '../fern/crypto'
import type { GroupState } from '../fern/state'
import { deriveGroupState } from '../fern/state'
import {
  getIdentity, saveIdentity, putEvent, getGroupEvents,
  getTips, putReceipt, putRelayPin, setMeta, getMeta,
  clearLocalData,
} from '../fern/db'
import { RelayClient, parseGroupAddress } from '../fern/relay'
import type { Receipt } from '../fern/relay'

export interface GroupEntry {
  pubkey: string
  name: string
  relays: string[]
}

export interface RelayConnection {
  client: RelayClient
  connected: boolean
  pubkey: string
}

export interface MessageDelivery {
  state: 'sending' | 'failed'
  ok: number
  total: number
  error?: string
}

async function publishToRelays(
  event: FernEvent,
  relays: string[],
): Promise<{ ok: number; total: number; error?: string }> {
  if (relays.length === 0) {
    return { ok: 0, total: 0, error: 'No relays configured for this group.' }
  }

  const results = await Promise.all(
    relays.map(async (url) => {
      const client = new RelayClient(url)
      try {
        await client.connect()
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
      } finally {
        await client.close()
      }
    }),
  )

  const ok = results.filter(Boolean).length
  return {
    ok,
    total: relays.length,
    error: ok === relays.length ? undefined : `${ok}/${relays.length} relays accepted the message.`,
  }
}

export function useBracken() {
  const [identity, setIdentity] = useState<Keypair | null>(null)
  const [groups, setGroups] = useState<GroupEntry[]>([])
  const [activeGroup, setActiveGroup] = useState<string | null>(null)
  const [events, setEvents] = useState<FernEvent[]>([])
  const [state, setState] = useState<GroupState | null>(null)
  const [relayConns, setRelayConns] = useState<RelayConnection[]>([])
  const [loading, setLoading] = useState(true)
  const [messageDeliveries, setMessageDeliveries] = useState<Record<string, MessageDelivery>>({})
  const clientsRef = useRef<Map<string, RelayClient>>(new Map())

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
    const group = groups.find((g) => g.pubkey === activeGroup)
    if (!group) return

    const newConns: RelayConnection[] = []
    const clientMap = clientsRef.current
    let cancelled = false

    ;(async () => {
      for (const url of group.relays) {
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

          if (!cancelled) {
            client.onEvent(async (event) => {
              try {
                await verifyEvent(event)
                await putEvent(event)
                if (event.group === activeGroup) {
                  const updated = await getGroupEvents(activeGroup)
                  setEvents(updated)
                  const { state: derived } = deriveGroupState(updated)
                  setState(derived)
                }
              } catch (e) {
                console.error('verifyEvent failed for', event.type, event.id?.slice(0, 16), e)
              }
            })
            await client.subscribe(activeGroup)

            const syncEvents = await client.sync(activeGroup)
            for (const event of syncEvents) {
              try {
                await verifyEvent(event)
                await putEvent(event)
              } catch (e) {
                console.error('catch-up sync verifyEvent failed for', event.type, event.id?.slice(0, 16), e)
              }
            }
            if (syncEvents.length > 0) {
              const updated = await getGroupEvents(activeGroup)
              setEvents(updated)
              const { state: derived } = deriveGroupState(updated)
              setState(derived)
            }

            newConns.push({
              client,
              connected: true,
              pubkey: client.relayPubkey,
            })
            clientMap.set(url, client)
          }
        } catch {
          // Connection failed — skip
        }
      }
      if (!cancelled) {
        setRelayConns(newConns)
      }
    })()

    return () => {
      cancelled = true
      for (const conn of newConns) {
        conn.client.close()
      }
      clientMap.clear()
      setRelayConns([])
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
    setRelayConns([])
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
          const syncedEvents = await client.sync(groupPubkey)
          for (const event of syncedEvents) {
            try {
              await verifyEvent(event)
              await putEvent(event)
            } catch (e) {
              console.error('sync verifyEvent failed for', event.type, event.id?.slice(0, 16), e)
            }
          }
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
    async (text: string): Promise<boolean> => {
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
        content: { text, channel: 'general' },
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
        const result = await publishToRelays(event, group.relays)
        setMessageDeliveries((prev) => {
          const next = { ...prev }
          if (result.total > 0 && result.ok === result.total) {
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
    [identity, activeGroup, groups, getPublishParents],
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

      const result = await publishToRelays(event, group.relays)
      setMessageDeliveries((prev) => {
        const next = { ...prev }
        if (result.total > 0 && result.ok === result.total) {
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
    [events, groups],
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
    async (name: string, relayUrls: string[]): Promise<void> => {
      if (!identity) throw new Error('No identity')
      const groupKeypair = generateKeypair()
      const input: EventInput = {
        type: 'genesis',
        group: groupKeypair.publicKey,
        author: identity.publicKey,
        parents: [],
        content: {
          name,
          description: '',
          public: true,
          founder: identity.publicKey,
          mods: [identity.publicKey],
          relays: relayUrls,
        },
        ts: Math.floor(Date.now() / 1000),
        tags: [],
      }
      const genesis = await buildEvent(input, identity, groupKeypair)
      await putEvent(genesis)

      for (const url of relayUrls) {
        try {
          const client = new RelayClient(url)
          await client.connect()
          await client.publish(genesis)
          await client.close()
        } catch {
          // best effort
        }
      }

      const groupEntry: GroupEntry = {
        pubkey: groupKeypair.publicKey,
        name,
        relays: relayUrls,
      }
      const updatedGroups = [...groups, groupEntry]
      setGroups(updatedGroups)
      await setMeta('groups', updatedGroups)
      setActiveGroup(groupKeypair.publicKey)
    },
    [identity, groups],
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
