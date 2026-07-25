import type { Commit, ValidatorSet } from './bft'
import type { FernEvent } from './events'
import { PROTOCOL_VERSION, canonicalJson, toWireEvent } from './events'
import { verifySignature } from './crypto'
import { validateValidatorSet } from './bft'
import { isValidEventId, isValidPubkey, isValidSig } from './utils'
import { log, shortId } from './logger'

export interface IngressReceipt {
  type: 'ingress_receipt'
  group: string
  chain_id: string
  epoch: number
  event_id: string
  validator: string
  first_seen_ms: number
  sig: string
}

export function verifyIngressReceipt(receipt: IngressReceipt): boolean {
  return isValidPubkey(receipt.group) && isValidEventId(receipt.chain_id) &&
    Number.isSafeInteger(receipt.epoch) && receipt.epoch >= 0 &&
    isValidEventId(receipt.event_id) && isValidPubkey(receipt.validator) &&
    Number.isSafeInteger(receipt.first_seen_ms) && receipt.first_seen_ms > 0 &&
    isValidSig(receipt.sig) && verifySignature(receipt.validator, canonicalJson([
    'ingress_receipt', receipt.group, receipt.chain_id, receipt.epoch,
    receipt.event_id, receipt.validator, receipt.first_seen_ms,
  ]), receipt.sig)
}

export interface ValidatorMetadata {
  protocol: string
  name: string
  description: string
  pubkey: string
  software: string
  version: string
  groups: string[]
  retention: { default: string }
  role: 'validator'
}

export interface ValidatorStatus {
  group: string
  chain_id: string
  height: number
  block_hash: string
  history_root: string
  state_root: string
  epoch: number
  validator_set: ValidatorSet
  logical_bytes: number
  validator: string
  sig: string
}

function statusPayload(status: ValidatorStatus): unknown[] {
  return [
    'validator_status', status.group, status.chain_id, status.height,
    status.block_hash, status.history_root, status.state_root, status.epoch,
    status.validator_set, status.logical_bytes, status.validator,
  ]
}

export function verifyValidatorStatus(status: ValidatorStatus, group: string): void {
  validateValidatorSet(status.validator_set)
  if (status.group !== group || !isValidPubkey(status.group) || !isValidEventId(status.chain_id) ||
    !Number.isSafeInteger(status.height) || status.height < 0 ||
    !Number.isSafeInteger(status.epoch) || status.epoch < 0 ||
    status.validator_set.epoch !== status.epoch ||
    !isValidEventId(status.block_hash) || !isValidEventId(status.history_root) ||
    !isValidEventId(status.state_root) || !Number.isSafeInteger(status.logical_bytes) ||
    status.logical_bytes < 0 || !status.validator_set.validators.some((item) => item.pubkey === status.validator) ||
    !isValidSig(status.sig) || !verifySignature(status.validator, canonicalJson(statusPayload(status)), status.sig))
    throw new Error('invalid signed validator status')
}

type PendingCallback = (event: FernEvent, receipt: IngressReceipt) => void
type CommitCallback = (commit: Commit) => void

export function validatorReconnectDelay(attempt: number): number {
  return Math.min(30_000, 1_000 * (2 ** Math.min(Math.max(attempt, 0), 5)))
}

export class ValidatorClient {
  readonly url: string
  validatorPubkey = ''
  private ws: WebSocket | null = null
  private connected = false
  private pendingCallbacks: PendingCallback[] = []
  private commitCallbacks: CommitCallback[] = []
  private closeCallbacks: (() => void)[] = []
  private resolver: ((message: Record<string, unknown>) => void) | null = null
  private requestChain: Promise<void> = Promise.resolve()
  private closing = false

  constructor(url: string) {
    this.url = url.startsWith('ws://') || url.startsWith('wss://') ? url : `ws://${url}`
  }

  get isConnected(): boolean {
    return this.connected && this.ws?.readyState === WebSocket.OPEN
  }

  async connect(timeoutMs = 5000): Promise<void> {
    if (this.isConnected) return
    this.closing = false
    log.debug('transport', 'connecting to validator', { url: this.url })
    this.ws = new WebSocket(this.url)
    await new Promise<void>((resolve, reject) => {
      if (!this.ws) return reject(new Error('WebSocket creation failed'))
      const timer = window.setTimeout(() => {
        log.warn('transport', 'validator connection timed out', { url: this.url })
        this.ws?.close()
        reject(new Error(`Connection to ${this.url} timed out`))
      }, timeoutMs)
      this.ws.onopen = () => {
        window.clearTimeout(timer)
        this.connected = true
        log.debug('transport', 'validator connection open', { url: this.url })
        resolve()
      }
      this.ws.onerror = () => {
        window.clearTimeout(timer)
        log.warn('transport', 'validator connection error', { url: this.url })
        reject(new Error(`Failed to connect to ${this.url}`))
      }
      this.ws.onclose = () => {
        window.clearTimeout(timer)
        this.connected = false
        this.resolver?.({ type: 'error', message: 'Validator connection closed' })
        if (!this.closing) log.warn('transport', 'validator connection closed', { url: this.url })
        else log.debug('transport', 'validator connection closed', { url: this.url })
        this.closeCallbacks.forEach((callback) => callback())
      }
      this.ws.onmessage = (event) => this.handleMessage(event)
    })
  }

  async close(): Promise<void> {
    this.closing = true
    this.connected = false
    this.ws?.close()
    this.ws = null
  }

  onPending(callback: PendingCallback): void { this.pendingCallbacks.push(callback) }
  onCommit(callback: CommitCallback): void { this.commitCallbacks.push(callback) }
  onClose(callback: () => void): void { this.closeCallbacks.push(callback) }

  waitForClose(): Promise<void> {
    if (!this.isConnected) return Promise.resolve()
    return new Promise((resolve) => this.onClose(resolve))
  }

  private handleMessage(event: MessageEvent): void {
    let message: Record<string, unknown>
    try { message = JSON.parse(String(event.data)) as Record<string, unknown> } catch {
      log.warn('transport', 'discarded non-JSON validator message', { url: this.url })
      return
    }
    if (message['type'] === 'pending_event') {
      this.pendingCallbacks.forEach((callback) => callback(
        message['event'] as FernEvent,
        message['ingress_receipt'] as IngressReceipt,
      ))
      return
    }
    if (message['type'] === 'commit') {
      this.commitCallbacks.forEach((callback) => callback(message['commit'] as Commit))
      return
    }
    this.resolver?.(message)
  }

  private async request<T>(action: string, extra: Record<string, unknown>, expected: string[]): Promise<T> {
    let release!: () => void
    const previous = this.requestChain
    this.requestChain = new Promise<void>((resolve) => { release = resolve })
    await previous
    try {
      if (!this.ws || !this.isConnected) throw new Error('Not connected')
      log.debug('transport', 'validator request', { url: this.url, action })
      return await new Promise<T>((resolve, reject) => {
        const timer = window.setTimeout(() => {
          this.resolver = null
          log.warn('transport', 'validator request timed out', { url: this.url, action })
          reject(new Error(`Timeout waiting for ${expected.join('/')}`))
        }, 15000)
        this.resolver = (message) => {
          const type = String(message['type'] ?? '')
          if (!expected.includes(type) && type !== 'error') return
          window.clearTimeout(timer)
          this.resolver = null
          if (type === 'error') {
            log.warn('transport', 'validator rejected request', {
              url: this.url, action, reason: String(message['message'] ?? 'validator error'),
            })
            reject(new Error(String(message['message'] ?? 'validator error')))
          } else {
            log.debug('transport', 'validator response', { url: this.url, action, type })
            resolve(message as T)
          }
        }
        this.ws!.send(JSON.stringify({ action, ...extra }))
      })
    } finally {
      release()
    }
  }

  async fetchMetadata(): Promise<ValidatorMetadata> {
    const response = await this.request<{ metadata: ValidatorMetadata }>('metadata', {}, ['metadata'])
    if (response.metadata.protocol !== PROTOCOL_VERSION || response.metadata.role !== 'validator' ||
      !isValidPubkey(response.metadata.pubkey)) throw new Error('invalid validator metadata')
    this.validatorPubkey = response.metadata.pubkey
    log.debug('transport', 'validator metadata verified', {
      url: this.url, validator: shortId(response.metadata.pubkey),
      groups: response.metadata.groups.length,
    })
    return response.metadata
  }

  async bootstrap(genesis: FernEvent): Promise<void> {
    await this.request('bootstrap', { genesis: toWireEvent(genesis) }, ['bootstrapped'])
  }

  async publish(event: FernEvent): Promise<IngressReceipt> {
    const response = await this.request<{ ingress_receipt: IngressReceipt }>(
      'submit_event', { event: toWireEvent(event) }, ['ingress_receipt'],
    )
    return response.ingress_receipt
  }

  async getGenesis(group: string): Promise<FernEvent | null> {
    const response = await this.request<{ type: string; genesis?: FernEvent }>(
      'get_genesis', { group }, ['genesis', 'not_found'],
    )
    return response.genesis ?? null
  }

  async status(group: string): Promise<ValidatorStatus> {
    const status = await this.request<ValidatorStatus>('status', { group }, ['status'])
    verifyValidatorStatus(status, group)
    return status
  }

  async getCommits(group: string, fromHeight = 1): Promise<Commit[]> {
    const response = await this.request<{ commits: Commit[] }>(
      'get_commits', { group, from_height: fromHeight, limit: 100 }, ['commits'],
    )
    return response.commits
  }

  async getPending(group: string): Promise<FernEvent[]> {
    const response = await this.request<{ events: FernEvent[] }>(
      'get_pending', { group }, ['pending_events'],
    )
    return response.events
  }

  async subscribe(group: string): Promise<void> {
    await this.request('subscribe', { group }, ['subscribed'])
  }
}

export async function fetchValidatorMetadata(url: string): Promise<ValidatorMetadata> {
  const client = new ValidatorClient(url)
  try {
    await client.connect()
    return await client.fetchMetadata()
  } finally {
    await client.close()
  }
}

export function parseGroupAddress(address: string): { groupPubkey: string; validators: string[] } {
  let value = address.trim()
  if (value.startsWith('http://') || value.startsWith('https://')) {
    try {
      const parsed = new URL(value)
      return {
        groupPubkey: parsed.searchParams.get('group') ?? '',
        validators: (parsed.searchParams.get('validators') ?? parsed.searchParams.get('relays') ?? '')
          .split(/[\s,]+/).filter(Boolean),
      }
    } catch { return { groupPubkey: '', validators: [] } }
  }
  if (value.startsWith('fern:')) value = value.slice(5)
  if (!value.includes('@')) return { groupPubkey: value, validators: [] }
  const [groupPubkey, raw] = value.split('@', 2)
  return { groupPubkey, validators: raw.split(',').map((item) => item.trim()).filter(Boolean) }
}

export interface GroupPreview {
  name: string
  description: string
  public: boolean
  founder: string
  admins: string[]
  canonicalValidators: string[]
  sourceValidator: string
}

export interface GroupPreviewError { error: string; unreachable: string[] }

export async function fetchGroupPreview(
  groupPubkey: string,
  validators: string[],
): Promise<GroupPreview | GroupPreviewError> {
  const unreachable: string[] = []
  for (const url of validators) {
    const client = new ValidatorClient(url)
    try {
      await client.connect()
      const genesis = await client.getGenesis(groupPubkey)
      if (!genesis) throw new Error('not found')
      const validators = Array.isArray(genesis.content['validators'])
        ? genesis.content['validators'] as { url?: unknown }[] : []
      return {
        name: String(genesis.content['name'] ?? 'Unnamed group'),
        description: String(genesis.content['description'] ?? ''),
        public: genesis.content['public'] !== false,
        founder: String(genesis.content['founder'] ?? ''),
        admins: Array.isArray(genesis.content['admins']) ? genesis.content['admins'] as string[] : [],
        canonicalValidators: validators.map((validator) => String(validator.url ?? '')).filter(Boolean),
        sourceValidator: url,
      }
    } catch { unreachable.push(url) } finally { await client.close() }
  }
  return { error: 'Could not load group info from any provided validator.', unreachable }
}
