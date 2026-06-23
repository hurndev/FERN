import type { FernEvent } from './events'

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

  constructor(url: string) {
    this.url = url
  }

  async connect(): Promise<void> {
    let wsUrl = this.url
    if (!wsUrl.startsWith('ws://') && !wsUrl.startsWith('wss://')) {
      wsUrl = `ws://${wsUrl}`
    }
    this.ws = new WebSocket(wsUrl)
    return new Promise((resolve, reject) => {
      if (!this.ws) return reject(new Error('WebSocket creation failed'))
      this.ws.onopen = () => {
        this.connected = true
        resolve()
      }
      this.ws.onerror = () => {
        if (!this.connected) reject(new Error(`Failed to connect to ${wsUrl}`))
      }
      this.ws.onclose = () => {
        this.connected = false
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
    if (this.ws) {
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
      resolver(msg)
    } else if (type === 'event') {
      const event = msg['event'] as FernEvent
      this.eventCallbacks.forEach((cb) => cb(event))
    } else if (type === 'group_status') {
      const att = msg['group_status'] as GroupStatus
      this.group_statusCallbacks.forEach((cb) => cb(att))
    }
  }

  private pendingResolvers = new Map<string, (msg: Record<string, unknown>) => void>()

  private async sendRequest<T>(
    expectedType: string | string[],
    action: string,
    extra?: Record<string, unknown>,
    timeout = 10000,
  ): Promise<T> {
    if (!this.ws || !this.isConnected) throw new Error('Not connected')
    return new Promise<T>((resolve, reject) => {
      const expectedTypes = Array.isArray(expectedType) ? expectedType : [expectedType]
      const cleanup = () => {
        for (const type of expectedTypes) this.pendingResolvers.delete(type)
      }
      const timer = setTimeout(() => {
        cleanup()
        reject(new Error(`Timeout waiting for ${expectedTypes.join(' or ')}`))
      }, timeout)
      const resolver = (msg: Record<string, unknown>) => {
        clearTimeout(timer)
        cleanup()
        if (msg['type'] === 'error') {
          reject(new Error(msg['message'] as string))
        } else {
          resolve(msg as unknown as T)
        }
      }
      for (const type of expectedTypes) this.pendingResolvers.set(type, resolver)
      const payload = { action, ...extra }
      this.ws!.send(JSON.stringify(payload))
    })
  }

  async subscribe(group: string): Promise<void> {
    if (!this.ws || !this.isConnected) throw new Error('Not connected')
    this.ws.send(JSON.stringify({ action: 'subscribe', group }))
  }

  async unsubscribe(group: string): Promise<void> {
    if (!this.ws || !this.isConnected) throw new Error('Not connected')
    this.ws.send(JSON.stringify({ action: 'unsubscribe', group }))
  }

  async publish(event: FernEvent): Promise<EventReceipt> {
    const msg = await this.sendRequest<{ event_receipt: EventReceipt }>(
      'event_receipt',
      'publish',
      { event },
    )
    return msg.event_receipt
  }

  async heal(event: FernEvent): Promise<EventReceipt> {
    const msg = await this.sendRequest<{ event_receipt: EventReceipt }>(
      'event_receipt',
      'heal',
      { event },
    )
    return msg.event_receipt
  }

  async get(eventId: string): Promise<FernEvent | null> {
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
    if (!this.ws || !this.isConnected) throw new Error('Not connected')
    return new Promise((resolve, reject) => {
      const events: FernEvent[] = []
      const cleanup = () => {
        this.pendingResolvers.delete('event')
        this.pendingResolvers.delete('sync_complete')
      }
      const timer = setTimeout(() => {
        cleanup()
        reject(new Error('Sync timeout'))
      }, 30000)
      const resolver = (msg: Record<string, unknown>) => {
        if (msg['type'] === 'event') {
          events.push(msg['event'] as FernEvent)
        } else if (msg['type'] === 'sync_complete') {
          clearTimeout(timer)
          cleanup()
          resolve(events)
        } else if (msg['type'] === 'error') {
          clearTimeout(timer)
          cleanup()
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
  }

  async syncIds(group: string): Promise<string[]> {
    const msg = await this.sendRequest<{ ids?: string[] }>(
      'ids',
      'sync_ids',
      { group },
    )
    return msg.ids ?? []
  }

  async syncLock(group: string, clientId: string): Promise<SyncLockResult> {
    const msg = await this.sendRequest<
      { type: string; ttl?: number; expires_in?: number }
    >(['sync_lock_granted', 'sync_lock_denied'], 'sync_lock', {
      group,
      client_id: clientId,
    })
    if (msg.type === 'sync_lock_granted') {
      return { granted: true, ttl: msg.ttl }
    }
    return { granted: false, expiresIn: msg.expires_in }
  }

  async syncUnlock(group: string, clientId: string): Promise<void> {
    await this.sendRequest('ok', 'sync_unlock', { group, client_id: clientId })
  }

  async requestGroupStatus(group: string): Promise<GroupStatus> {
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
    const resp = await fetch(metaUrl)
    const data = await resp.json()
    this.relayPubkey = data.pubkey ?? ''
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
}

export async function fetchRelayMetadata(url: string): Promise<RelayMetadata> {
  const metaUrl = url.replace('wss://', 'https://').replace('ws://', 'http://')
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
