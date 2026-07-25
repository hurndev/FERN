import type { Keypair } from './crypto'
import { sign, verifySignature } from './crypto'
import {
  MAX_EVENT_BYTES,
  MAX_TAG_ITEMS,
  MAX_TAG_STRING_BYTES,
  MAX_TAGS,
  MAX_TYPE_BYTES,
} from './limits'
import { sha256Hex, isValidPubkey, isValidEventId, isValidSig } from './utils'

export const PROTOCOL_VERSION = 'fern-bft-1'

export interface ConsensusPosition {
  status: 'pending' | 'finalized'
  height?: number
  position?: number
  certifiedTimeMs?: number
}

export interface FernEvent {
  protocol: typeof PROTOCOL_VERSION
  id: string
  type: string
  group: string
  author: string
  seq: number
  content: Record<string, unknown>
  ts: number
  tags: string[][]
  sig: string
  bft?: ConsensusPosition
}

export type EventInput = Omit<FernEvent, 'protocol' | 'id' | 'sig' | 'bft'> & {
  protocol?: typeof PROTOCOL_VERSION
}

export type WireEvent = Omit<FernEvent, 'bft'>

function compareCodePoints(a: string, b: string): number {
  const left = Array.from(a, (value) => value.codePointAt(0)!)
  const right = Array.from(b, (value) => value.codePointAt(0)!)
  for (let index = 0; index < Math.max(left.length, right.length); index++) {
    if (left[index] === undefined) return -1
    if (right[index] === undefined) return 1
    if (left[index] !== right[index]) return left[index] - right[index]
  }
  return 0
}

export function sortKeysDeep(obj: unknown): unknown {
  if (obj === null || typeof obj === 'boolean' || typeof obj === 'string') return obj
  if (typeof obj === 'number') {
    if (!Number.isSafeInteger(obj)) throw new TypeError('canonical JSON permits only safe integers')
    return obj
  }
  if (Array.isArray(obj)) return obj.map(sortKeysDeep)
  if (typeof obj !== 'object') throw new TypeError(`value is not canonical JSON: ${typeof obj}`)
  const sorted: Record<string, unknown> = {}
  for (const key of Object.keys(obj as Record<string, unknown>).sort(compareCodePoints)) {
    sorted[key] = sortKeysDeep((obj as Record<string, unknown>)[key])
  }
  return sorted
}

export function canonicalJson(value: unknown): Uint8Array {
  return new TextEncoder().encode(JSON.stringify(sortKeysDeep(value)))
}

export function toWireEvent(event: FernEvent): WireEvent {
  return {
    protocol: event.protocol,
    id: event.id,
    type: event.type,
    group: event.group,
    author: event.author,
    seq: event.seq,
    content: event.content,
    ts: event.ts,
    tags: event.tags,
    sig: event.sig,
  }
}

export function canonicalSerialization(event: EventInput | FernEvent): Uint8Array {
  const tags = [...event.tags].sort((a, b) => {
    for (let i = 0; i < Math.max(a.length, b.length); i++) {
      if (i >= a.length) return -1
      if (i >= b.length) return 1
      const compared = compareCodePoints(a[i], b[i])
      if (compared) return compared
    }
    return 0
  })
  return canonicalJson([
    event.protocol ?? PROTOCOL_VERSION,
    event.type,
    event.group,
    event.author,
    event.seq,
    event.content,
    event.ts,
    tags,
  ])
}

export async function computeId(event: EventInput | FernEvent): Promise<string> {
  return sha256Hex(canonicalSerialization(event))
}

export async function buildEvent(
  input: EventInput,
  keypair: Keypair,
  groupKeypair?: Keypair,
): Promise<FernEvent> {
  const normalized = { ...input, protocol: PROTOCOL_VERSION } as EventInput
  const signingKey = input.type === 'genesis' ? (groupKeypair ?? keypair) : keypair
  const bytes = canonicalSerialization(normalized)
  return {
    ...normalized,
    protocol: PROTOCOL_VERSION,
    id: await sha256Hex(bytes),
    sig: sign(signingKey.secretKey, bytes),
  }
}

export class VerificationError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'VerificationError'
  }
}

const EVENT_FIELDS = new Set([
  'protocol', 'id', 'type', 'group', 'author', 'seq', 'content', 'ts', 'tags', 'sig',
])
const textEncoder = new TextEncoder()

export async function verifyEvent(event: FernEvent): Promise<void> {
  if (typeof event !== 'object' || event === null || Array.isArray(event))
    throw new VerificationError('event must be an object')
  const keys = Object.keys(event)
  for (const field of EVENT_FIELDS) {
    if (!keys.includes(field)) throw new VerificationError(`missing field: ${field}`)
  }
  for (const key of keys) {
    if (!EVENT_FIELDS.has(key)) throw new VerificationError(`unsigned extra field: ${key}`)
  }
  if (event.protocol !== PROTOCOL_VERSION)
    throw new VerificationError(`unsupported protocol: ${event.protocol}`)
  if (textEncoder.encode(JSON.stringify(toWireEvent(event))).length > MAX_EVENT_BYTES)
    throw new VerificationError('event exceeds 32 KiB')
  if (!event.type || textEncoder.encode(event.type).length > MAX_TYPE_BYTES)
    throw new VerificationError('invalid event type')
  if (!isValidPubkey(event.group) || !isValidPubkey(event.author))
    throw new VerificationError('invalid group or author key')
  if (!isValidEventId(event.id) || !isValidSig(event.sig))
    throw new VerificationError('invalid event id or signature')
  if (!Number.isInteger(event.seq) || (event.type === 'genesis' ? event.seq !== 0 : event.seq < 1))
    throw new VerificationError('invalid author sequence')
  if (!Number.isInteger(event.ts) || event.ts <= 0)
    throw new VerificationError('ts must be a positive integer')
  if (typeof event.content !== 'object' || event.content === null || Array.isArray(event.content))
    throw new VerificationError('content must be a JSON object')
  if (!Array.isArray(event.tags) || event.tags.length > MAX_TAGS)
    throw new VerificationError('invalid tags')
  for (const tag of event.tags) {
    if (!Array.isArray(tag) || tag.length > MAX_TAG_ITEMS)
      throw new VerificationError('invalid tag')
    for (const item of tag) {
      if (typeof item !== 'string' || textEncoder.encode(item).length > MAX_TAG_STRING_BYTES)
        throw new VerificationError('invalid tag item')
    }
  }
  const id = await computeId(event)
  if (id !== event.id) throw new VerificationError('event id mismatch')
  const signingKey = event.type === 'genesis' ? event.group : event.author
  if (!verifySignature(signingKey, canonicalSerialization(event), event.sig))
    throw new VerificationError('invalid event signature')
}
