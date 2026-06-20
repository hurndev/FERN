import type { FernEvent } from './events'

export interface Receipt {
  event_id: string
  group: string
  relay: string
  ts: number
  sig: string
}

export interface Attestation {
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

type EventCallback = (event: FernEvent) => void
type AttestationCallback = (att: Attestation) => void

export class RelayClient {
  url: string
  relayPubkey = ''
  private ws: WebSocket | null = null
  private eventCallbacks: EventCallback[] = []
  private attestationCallbacks: AttestationCallback[] = []
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
      }
      this.ws.onmessage = (ev) => this.handleMessage(ev)
    })
  }

  get isConnected(): boolean {
    return this.connected && this.ws?.readyState === WebSocket.OPEN
  }

  async close(): Promise<void> {
    this.connected = false
    if (this.ws) {
      this.ws.close()
      this.ws = null
    }
  }

  onEvent(cb: EventCallback): void {
    this.eventCallbacks.push(cb)
  }

  onAttestation(cb: AttestationCallback): void {
    this.attestationCallbacks.push(cb)
  }

  private handleMessage(ev: MessageEvent): void {
    let msg: Record<string, unknown>
    try {
      msg = JSON.parse(ev.data as string)
    } catch {
      return
    }
    const type = msg['type'] as string
    if (type === 'event') {
      const event = msg['event'] as FernEvent
      this.eventCallbacks.forEach((cb) => cb(event))
    } else if (type === 'attestation') {
      const att = msg['attestation'] as Attestation
      this.attestationCallbacks.forEach((cb) => cb(att))
    } else {
      const resolver =
        this.pendingResolvers.get(type) ??
        (type === 'error' ? [...this.pendingResolvers.values()][0] : undefined)
      if (resolver) {
        resolver(msg)
      }
    }
  }

  private pendingResolvers = new Map<string, (msg: Record<string, unknown>) => void>()

  private async sendRequest<T>(
    expectedType: string,
    action: string,
    extra?: Record<string, unknown>,
    timeout = 10000,
  ): Promise<T> {
    if (!this.ws || !this.isConnected) throw new Error('Not connected')
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pendingResolvers.delete(expectedType)
        reject(new Error(`Timeout waiting for ${expectedType}`))
      }, timeout)
      this.pendingResolvers.set(expectedType, (msg) => {
        clearTimeout(timer)
        this.pendingResolvers.delete(expectedType)
        if (msg['type'] === 'error') {
          reject(new Error(msg['message'] as string))
        } else {
          resolve(msg as unknown as T)
        }
      })
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

  async publish(event: FernEvent): Promise<Receipt> {
    const msg = await this.sendRequest<{ receipt: Receipt }>(
      'receipt',
      'publish',
      { event },
    )
    return msg.receipt
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
      const timer = setTimeout(() => {
        reject(new Error('Sync timeout'))
      }, 30000)
      const handler = (ev: MessageEvent) => {
        let msg: Record<string, unknown>
        try {
          msg = JSON.parse(ev.data as string)
        } catch {
          return
        }
        if (msg['type'] === 'event') {
          events.push(msg['event'] as FernEvent)
        } else if (msg['type'] === 'sync_complete') {
          clearTimeout(timer)
          this.ws?.removeEventListener('message', handler)
          resolve(events)
        } else if (msg['type'] === 'error') {
          clearTimeout(timer)
          this.ws?.removeEventListener('message', handler)
          reject(new Error(msg['message'] as string))
        }
      }
      const ws = this.ws
      if (!ws) {
        clearTimeout(timer)
        reject(new Error('Not connected'))
        return
      }
      ws.addEventListener('message', handler)
      const payload: Record<string, unknown> = { action: 'sync', group }
      if (sinceTs !== undefined) payload['since'] = sinceTs
      ws.send(JSON.stringify(payload))
    })
  }

  async requestAttestation(group: string): Promise<Attestation> {
    const msg = await this.sendRequest<{ attestation: Attestation }>(
      'attestation',
      'attestation',
      { group },
    )
    return msg.attestation
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
  let addr = address
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
