import type { FernEvent } from './events'
import { log } from './logger'
import type {
  HealChallenge,
  GroupHostAttestation,
  InventoryAttestation,
  InventoryAttestationResult,
  HealBatchResult,
} from './heal_attestations'

export interface EventReceipt {
  event_id: string
  group: string
  relay: string
  ts: number
  sig: string
}

export interface GroupStatus {
  group: string
  relay: string
  set_hash: string
  tips: string[]
  count: number
  prev: string | null
  ts: number
  sig: string
}

export interface RelayMetadata {
  name: string
  description: string
  pubkey: string
  software: string
  version: string
  groups: string[]
  retention: string
}

export interface SyncLockResult {
  granted: boolean
  ttl?: number
  expiresIn?: number
}

type EventCallback = (event: FernEvent) => void
type GroupStatusCallback = (att: GroupStatus) => void

export class RelayClient {
  url: string
  relayPubkey = ''
  private ws: WebSocket | null = null
  private eventCallbacks: EventCallback[] = []
  private group_statusCallbacks: GroupStatusCallback[] = []
  private closeCallbacks: (() => void)[] = []
  private connected = false
  private pendingResolvers = new Map<string, (msg: Record<string, unknown>) => void>()
  private requestQueues = new Map<string, { queue: Array<() => void>; processing: boolean }>()

  constructor(url: string) {
    this.url = url
  }

  async connect(): Promise<void> {
    let wsUrl = this.url
    if (!wsUrl.startsWith('ws://') && !wsUrl.startsWith('wss://')) {
      wsUrl = `ws://${wsUrl}`
    }
    log.relayConnect(wsUrl)
    this.ws = new WebSocket(wsUrl)
    return new Promise((resolve, reject) => {
      if (!this.ws) return reject(new Error('WebSocket creation failed'))
      this.ws.onopen = () => {
        this.connected = true
        log.relayConnected(wsUrl, this.relayPubkey)
        resolve()
      }
      this.ws.onerror = () => {
        if (!this.connected) {
          log.relayConnectFailed(wsUrl, 'WebSocket error')
          reject(new Error(`Failed to connect to ${wsUrl}`))
        }
      }
      this.ws.onclose = () => {
        this.connected = false
        this.pendingResolvers.clear()
        for (const [, entry] of this.requestQueues) {
          for (const resolve of entry.queue) resolve()
          entry.queue = []
          entry.processing = false
        }
        log.relayClosed(wsUrl)
        this.closeCallbacks.forEach((cb) => cb())
      }
      this.ws.onmessage = (ev) => this.handleMessage(ev)
    })
  }

  get isConnected(): boolean {
    return this.connected && this.ws?.readyState === WebSocket.OPEN
  }

  async close(): Promise<void> {
    this.connected = false
    this.closeCallbacks = []
    for (const [, entry] of this.requestQueues) {
      for (const resolve of entry.queue) resolve()
      entry.queue = []
      entry.processing = false
    }
    this.requestQueues.clear()
    if (this.ws) {
      log.relayClosed(this.url)
      this.ws.close()
      this.ws = null
    }
  }

  onEvent(cb: EventCallback): void {
    this.eventCallbacks.push(cb)
  }

  onGroupStatus(cb: GroupStatusCallback): void {
    this.group_statusCallbacks.push(cb)
  }

  onClose(cb: () => void): void {
    this.closeCallbacks.push(cb)
  }

  private handleMessage(ev: MessageEvent): void {
    let msg: Record<string, unknown>
    try {
      msg = JSON.parse(ev.data as string)
    } catch {
      return
    }
    const type = msg['type'] as string
    const resolver =
      this.pendingResolvers.get(type) ??
      (type === 'error' ? [...this.pendingResolvers.values()][0] : undefined)
    if (resolver) {
      log.relayResponse(this.url, type)
      resolver(msg)
    } else if (type === 'event') {
      const event = msg['event'] as FernEvent
      log.relayPushEvent(this.url, event.type, event.id)
      this.eventCallbacks.forEach((cb) => cb(event))
    } else if (type === 'group_status') {
      const att = msg['group_status'] as GroupStatus
      log.relayPushGroupStatus(this.url, att.group, att.set_hash, att.count)
      this.group_statusCallbacks.forEach((cb) => cb(att))
    }
  }

  private enqueueRequest(queueKey: string): Promise<void> {
    let entry = this.requestQueues.get(queueKey)
    if (!entry) {
      entry = { queue: [], processing: false }
      this.requestQueues.set(queueKey, entry)
    }

    if (!entry.processing) {
      entry.processing = true
      return Promise.resolve()
    }

    return new Promise<void>((resolve) => {
      entry!.queue.push(resolve)
      log.relayQueueEnqueue(this.url, queueKey, entry!.queue.length)
    })
  }

  private dequeueRequest(queueKey: string): void {
    const entry = this.requestQueues.get(queueKey)
    if (!entry) return

    const next = entry.queue.shift()
    if (next) {
      next()
    } else {
      entry.processing = false
    }
  }

  private async sendRequest<T>(
    expectedType: string | string[],
    action: string,
    extra?: Record<string, unknown>,
    timeout = 10000,
  ): Promise<T> {
    const expectedTypes = Array.isArray(expectedType) ? expectedType : [expectedType]
    const queueKey = expectedTypes[0]

    const enqueuedAt = Date.now()
    await this.enqueueRequest(queueKey)
    const waitMs = Date.now() - enqueuedAt
    if (waitMs > 0) log.relayQueueDequeue(this.url, queueKey, waitMs)

    if (!this.ws || !this.isConnected) {
      this.dequeueRequest(queueKey)
      throw new Error('Not connected')
    }

    try {
      return await new Promise<T>((resolve, reject) => {
        const cleanup = () => {
          for (const type of expectedTypes) this.pendingResolvers.delete(type)
        }
        const timer = setTimeout(() => {
          cleanup()
          log.relayTimeout(this.url, expectedTypes.join('/'), timeout)
          reject(new Error(`Timeout waiting for ${expectedTypes.join(' or ')}`))
        }, timeout)
        const resolver = (msg: Record<string, unknown>) => {
          clearTimeout(timer)
          cleanup()
          if (msg['type'] === 'error') {
            log.relayError(this.url, `error response to ${action}: ${msg['message']}`)
            reject(new Error(msg['message'] as string))
          } else {
            resolve(msg as unknown as T)
          }
        }
        for (const type of expectedTypes) this.pendingResolvers.set(type, resolver)
        const payload = { action, ...extra }
        log.relaySend(this.url, action)
        this.ws!.send(JSON.stringify(payload))
      })
    } finally {
      this.dequeueRequest(queueKey)
    }
  }

  async subscribe(group: string): Promise<void> {
    if (!this.ws || !this.isConnected) throw new Error('Not connected')
    log.relaySend(this.url, 'subscribe', `group=${group.slice(0, 12)}…`)
    this.ws.send(JSON.stringify({ action: 'subscribe', group }))
  }

  async unsubscribe(group: string): Promise<void> {
    if (!this.ws || !this.isConnected) throw new Error('Not connected')
    log.relaySend(this.url, 'unsubscribe', `group=${group.slice(0, 12)}…`)
    this.ws.send(JSON.stringify({ action: 'unsubscribe', group }))
  }

  async publish(event: FernEvent): Promise<EventReceipt> {
    log.relaySend(this.url, 'publish', `${event.type} ${event.id.slice(0, 12)}…`)
    const msg = await this.sendRequest<{ event_receipt: EventReceipt }>(
      'event_receipt',
      'publish',
      { event },
    )
    log.relayResponse(this.url, 'event_receipt', `for ${event.id.slice(0, 12)}…`)
    return msg.event_receipt
  }

  async heal(event: FernEvent): Promise<EventReceipt> {
    log.relaySend(this.url, 'heal', `${event.type} ${event.id.slice(0, 12)}…`)
    const msg = await this.sendRequest<{ event_receipt: EventReceipt }>(
      'event_receipt',
      'heal',
      { event },
    )
    return msg.event_receipt
  }

  async get(eventId: string): Promise<FernEvent | null> {
    log.relaySend(this.url, 'get', `id=${eventId.slice(0, 12)}…`)
    try {
      const msg = await this.sendRequest<{ event?: FernEvent }>(
        'event',
        'get',
        { id: eventId },
      )
      return msg.event ?? null
    } catch {
      return null
    }
  }

  async sync(group: string, sinceTs?: number): Promise<FernEvent[]> {
    const queueKey = 'event'
    const enqueuedAt = Date.now()
    await this.enqueueRequest(queueKey)
    const waitMs = Date.now() - enqueuedAt
    if (waitMs > 0) log.relayQueueDequeue(this.url, queueKey, waitMs)

    if (!this.ws || !this.isConnected) {
      this.dequeueRequest(queueKey)
      throw new Error('Not connected')
    }

    log.relaySend(this.url, 'sync', `group=${group.slice(0, 12)}…${sinceTs !== undefined ? ` since=${sinceTs}` : ''}`)
    try {
      return await new Promise<FernEvent[]>((resolve, reject) => {
        const events: FernEvent[] = []
        const cleanup = () => {
          this.pendingResolvers.delete('event')
          this.pendingResolvers.delete('sync_complete')
        }
        const timer = setTimeout(() => {
          cleanup()
          log.relayTimeout(this.url, 'sync_complete', 30000)
          reject(new Error('Sync timeout'))
        }, 30000)
        const resolver = (msg: Record<string, unknown>) => {
          if (msg['type'] === 'event') {
            events.push(msg['event'] as FernEvent)
          } else if (msg['type'] === 'sync_complete') {
            clearTimeout(timer)
            cleanup()
            log.relayResponse(this.url, 'sync_complete', `${events.length} events`)
            resolve(events)
          } else if (msg['type'] === 'error') {
            clearTimeout(timer)
            cleanup()
            log.relayError(this.url, `sync error: ${msg['message']}`)
            reject(new Error(msg['message'] as string))
          }
        }
        const ws = this.ws
        if (!ws) {
          clearTimeout(timer)
          reject(new Error('Not connected'))
          return
        }
        this.pendingResolvers.set('event', resolver)
        this.pendingResolvers.set('sync_complete', resolver)
        const payload: Record<string, unknown> = { action: 'sync', group }
        if (sinceTs !== undefined) payload['since'] = sinceTs
        ws.send(JSON.stringify(payload))
      })
    } finally {
      this.dequeueRequest(queueKey)
    }
  }

  async syncIds(group: string): Promise<string[]> {
    log.relaySend(this.url, 'sync_ids', `group=${group.slice(0, 12)}…`)
    const msg = await this.sendRequest<{ ids?: string[] }>(
      'ids',
      'sync_ids',
      { group },
    )
    const ids = msg.ids ?? []
    log.relayResponse(this.url, 'ids', `${ids.length} ids`)
    return ids
  }

  async syncLock(group: string, clientId: string): Promise<SyncLockResult> {
    log.relaySend(this.url, 'sync_lock', `group=${group.slice(0, 12)}…`)
    const msg = await this.sendRequest<
      { type: string; ttl?: number; expires_in?: number }
    >(['sync_lock_granted', 'sync_lock_denied'], 'sync_lock', {
      group,
      client_id: clientId,
    })
    if (msg.type === 'sync_lock_granted') {
      log.relayResponse(this.url, 'sync_lock_granted', `ttl=${msg.ttl}`)
      return { granted: true, ttl: msg.ttl }
    }
    log.relayResponse(this.url, 'sync_lock_denied', `expires_in=${msg.expires_in}`)
    return { granted: false, expiresIn: msg.expires_in }
  }

  async syncUnlock(group: string, clientId: string): Promise<void> {
    log.relaySend(this.url, 'sync_unlock', `group=${group.slice(0, 12)}…`)
    await this.sendRequest('ok', 'sync_unlock', { group, client_id: clientId })
  }

  async requestGroupStatus(group: string): Promise<GroupStatus> {
    log.relaySend(this.url, 'group_status', `group=${group.slice(0, 12)}…`)
    const msg = await this.sendRequest<{ group_status: GroupStatus }>(
      'group_status',
      'group_status',
      { group },
    )
    return msg.group_status
  }

  async fetchMetadata(): Promise<RelayMetadata> {
    const metaUrl = this.url
      .replace('wss://', 'https://')
      .replace('ws://', 'http://')
    log.relaySend(this.url, 'fetchMetadata', metaUrl)
    try {
      const resp = await fetch(metaUrl)
      const data = await resp.json()
      this.relayPubkey = data.pubkey ?? ''
      const meta = {
        name: data.name ?? '',
        description: data.description ?? '',
        pubkey: data.pubkey ?? '',
        software: data.software ?? '',
        version: data.version ?? '',
        groups: data.groups ?? [],
        retention: data.retention?.default ?? 'full',
      }
      log.relayMetadata(this.url, meta)
      return meta
    } catch (err) {
      log.relayMetadataFailed(this.url, err)
      throw err
    }
  }

  async getHealChallenge(group: string, ids: string[]): Promise<HealChallenge> {
    log.relaySend(this.url, 'get_heal_challenge', `group=${group.slice(0, 12)}… ${ids.length} ids`)
    const msg = await this.sendRequest<{ heal_challenge: HealChallenge }>(
      'heal_challenge',
      'get_heal_challenge',
      { group, ids },
    )
    return msg.heal_challenge
  }

  async getGroupHostAttestation(challenge: HealChallenge): Promise<GroupHostAttestation> {
    log.relaySend(this.url, 'get_group_host_attestation', `challenge=${challenge.group.slice(0, 12)}…`)
    const msg = await this.sendRequest<{ group_host_attestation: GroupHostAttestation }>(
      'group_host_attestation',
      'get_group_host_attestation',
      { heal_challenge: challenge },
    )
    return msg.group_host_attestation
  }

  async getInventoryAttestation(
    challenge: HealChallenge,
    ids: string[],
  ): Promise<InventoryAttestationResult> {
    log.relaySend(this.url, 'get_inventory_attestation', `${ids.length} ids`)
    const msg = await this.sendRequest<{
      type: string
      attestation?: InventoryAttestation
      ids?: string[]
      missing?: string[]
    }>(
      ['inventory_attestation', 'inventory_missing'],
      'get_inventory_attestation',
      { heal_challenge: challenge, ids },
      15000,
    )
    if (msg.type === 'inventory_missing') {
      log.relayResponse(this.url, 'inventory_missing', `${(msg.missing as string[])?.length ?? 0} missing`)
      return {
        attestation: null,
        covered: [],
        missing: (msg.missing as string[]) ?? [],
        inventoryMissing: true,
      }
    }
    const covered = (msg.ids as string[]) ?? []
    log.relayResponse(this.url, 'inventory_attestation', `${covered.length} covered`)
    return {
      attestation: (msg.attestation as InventoryAttestation) ?? null,
      covered,
      missing: (msg.missing as string[]) ?? [],
      inventoryMissing: false,
    }
  }

  async healBatch(
    challenge: HealChallenge,
    events: FernEvent[],
    hostAtts: GroupHostAttestation[],
    invAtts: { attestation: InventoryAttestation; ids: string[] }[],
  ): Promise<HealBatchResult> {
    log.relaySend(this.url, 'heal_batch', `${events.length} events, ${hostAtts.length} host atts, ${invAtts.length} inv atts`)
    const msg = await this.sendRequest<{ heal_batch_result: HealBatchResult }>(
      'heal_batch_result',
      'heal_batch',
      {
        heal_challenge: challenge,
        events,
        group_host_attestations: hostAtts,
        inventory_attestations: invAtts,
      },
      30000,
    )
    const result = msg.heal_batch_result
    log.relayResponse(this.url, 'heal_batch_result', `stored=${result.stored.length} rejected=${result.rejected.length}`)
    return result
  }
}

export async function fetchRelayMetadata(url: string): Promise<RelayMetadata> {
  const metaUrl = url.replace('wss://', 'https://').replace('ws://', 'http://')
  log.relaySend(url, 'fetchMetadata', metaUrl)
  const resp = await fetch(metaUrl)
  const data = await resp.json()
  return {
    name: data.name ?? '',
    description: data.description ?? '',
    pubkey: data.pubkey ?? '',
    software: data.software ?? '',
    version: data.version ?? '',
    groups: data.groups ?? [],
    retention: data.retention?.default ?? 'full',
  }
}

export function parseGroupAddress(address: string): {
  groupPubkey: string
  relays: string[]
} {
  let addr = address.trim()

  if (addr.startsWith('http://') || addr.startsWith('https://')) {
    try {
      const url = new URL(addr)
      const groupPubkey = (url.searchParams.get('group') ?? '').trim()
      const relaysParam = (url.searchParams.get('relays') ?? '').trim()
      const relays = relaysParam
        .split(/[\s,]+/)
        .map((r) => r.trim())
        .filter(Boolean)
      return { groupPubkey, relays }
    } catch {
      return { groupPubkey: '', relays: [] }
    }
  }

  if (addr.startsWith('fern:')) addr = addr.slice(5)
  if (addr.includes('@')) {
    const [groupPubkey, relaysPart] = addr.split('@', 2)
    const relays = relaysPart
      .split(',')
      .map((r) => r.trim())
      .filter(Boolean)
    return { groupPubkey, relays }
  }
  return { groupPubkey: addr, relays: [] }
}

export interface GroupPreview {
  name: string
  description: string
  public: boolean
  founder: string
  admins: string[]
  canonicalRelays: string[]
  sourceRelay: string
}

export interface GroupPreviewError {
  error: string
  unreachable: string[]
}

export async function fetchGroupPreview(
  groupPubkey: string,
  relays: string[],
): Promise<GroupPreview | GroupPreviewError> {
  const unreachable: string[] = []
  for (const url of relays) {
    const client = new RelayClient(url)
    try {
      await client.connect()
    } catch {
      unreachable.push(url)
      continue
    }
    try {
      const events = await client.sync(groupPubkey)
      const genesis = events.find((e) => e.type === 'genesis' && e.group === groupPubkey)
      if (!genesis) {
        unreachable.push(url)
        continue
      }
      const content = genesis.content as Record<string, unknown>
      const canonicalRelays = Array.isArray(content['relays'])
        ? (content['relays'] as unknown[]).filter((r): r is string => typeof r === 'string')
        : []
      return {
        name: typeof content['name'] === 'string' ? (content['name'] as string) : 'Unnamed group',
        description: typeof content['description'] === 'string' ? (content['description'] as string) : '',
        public: content['public'] !== false,
        founder: typeof content['founder'] === 'string' ? (content['founder'] as string) : '',
        admins: Array.isArray(content['admins'])
          ? (content['admins'] as unknown[]).filter((m): m is string => typeof m === 'string')
          : [],
        canonicalRelays,
        sourceRelay: url,
      }
    } catch {
      unreachable.push(url)
    } finally {
      await client.close()
    }
  }
  return {
    error: 'Could not load group info from any provided relay.',
    unreachable,
  }
}
