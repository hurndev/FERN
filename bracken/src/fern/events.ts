import type { Keypair } from './crypto'
import { sign, verifySignature } from './crypto'
import { MAX_EVENT_BYTES, MAX_PARENTS, MAX_TAG_ITEMS, MAX_TAG_STRING_BYTES, MAX_TAGS, MAX_TYPE_BYTES } from './limits'
import { sha256Hex, isValidPubkey, isValidEventId, isValidSig } from './utils'

export interface FernEvent {
  id: string
  type: string
  group: string
  author: string
  parents: string[]
  content: Record<string, unknown>
  ts: number
  tags: string[][]
  sig: string
}

export type EventInput = Omit<FernEvent, 'id' | 'sig'>

function sortKeysDeep(obj: unknown): unknown {
  if (obj === null || typeof obj !== 'object') return obj
  if (Array.isArray(obj)) return obj.map(sortKeysDeep)
  const sorted: Record<string, unknown> = {}
  for (const key of Object.keys(obj as Record<string, unknown>).sort()) {
    sorted[key] = sortKeysDeep((obj as Record<string, unknown>)[key])
  }
  return sorted
}

export function canonicalSerialization(event: EventInput | FernEvent): Uint8Array {
  const parents = [...event.parents].sort()
  const content = sortKeysDeep(event.content)
  const tags = [...event.tags].sort((a, b) => {
    for (let i = 0; i < Math.max(a.length, b.length); i++) {
      const av = a[i] ?? ''
      const bv = b[i] ?? ''
      if (av < bv) return -1
      if (av > bv) return 1
    }
    return 0
  })
  const arr = [
    event.type,
    event.group,
    event.author,
    parents,
    content,
    event.ts,
    tags,
  ]
  const json = JSON.stringify(arr)
  return new TextEncoder().encode(json)
}

export async function computeId(event: EventInput | FernEvent): Promise<string> {
  const bytes = canonicalSerialization(event)
  return sha256Hex(bytes)
}

export async function buildEvent(
  input: EventInput,
  keypair: Keypair,
  groupKeypair?: Keypair,
): Promise<FernEvent> {
  const isGenesis = input.type === 'genesis'
  const signingKey = isGenesis ? (groupKeypair ?? keypair) : keypair

  const bytes = canonicalSerialization(input)
  const sig = sign(signingKey.secretKey, bytes)
  const id = await sha256Hex(bytes)

  return { ...input, id, sig }
}

export class VerificationError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'VerificationError'
  }
}

const EVENT_FIELDS = new Set(['id', 'type', 'group', 'author', 'parents', 'content', 'ts', 'tags', 'sig'])
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
  if (textEncoder.encode(JSON.stringify(event)).length > MAX_EVENT_BYTES)
    throw new VerificationError('event exceeds 32 MiB')
  if (!event.type || typeof event.type !== 'string')
    throw new VerificationError('type must be a non-empty string')
  if (textEncoder.encode(event.type).length > MAX_TYPE_BYTES)
    throw new VerificationError('type exceeds maximum length')
  if (!isValidPubkey(event.group))
    throw new VerificationError('group must be 64-char lowercase hex')
  if (!isValidPubkey(event.author))
    throw new VerificationError('author must be 64-char lowercase hex')
  if (event.id && !isValidEventId(event.id))
    throw new VerificationError('id must be 64-char lowercase hex')
  if (event.sig && !isValidSig(event.sig))
    throw new VerificationError('sig must be 128-char lowercase hex')
  if (!Number.isInteger(event.ts) || event.ts <= 0)
    throw new VerificationError('ts must be a positive integer')
  if (typeof event.content !== 'object' || event.content === null || Array.isArray(event.content))
    throw new VerificationError('content must be a JSON object')
  if (!Array.isArray(event.parents))
    throw new VerificationError('parents must be an array')
  if (event.type === 'genesis' && event.parents.length !== 0)
    throw new VerificationError('genesis must have empty parents')
  if (event.type !== 'genesis' && event.parents.length === 0)
    throw new VerificationError('non-genesis event must have at least one parent')
  if (event.type !== 'genesis' && event.parents.length > MAX_PARENTS)
    throw new VerificationError('too many parents')
  const uniqueParents = new Set(event.parents)
  if (uniqueParents.size !== event.parents.length)
    throw new VerificationError('parents must be unique')
  for (const p of event.parents) {
    if (!isValidEventId(p))
      throw new VerificationError(`parent '${p.slice(0, 20)}...' must be 64-char lowercase hex`)
  }
  if (!Array.isArray(event.tags))
    throw new VerificationError('tags must be an array')
  if (event.tags.length > MAX_TAGS)
    throw new VerificationError('too many tags')
  for (const tag of event.tags) {
    if (!Array.isArray(tag))
      throw new VerificationError('each tag must be an array')
    if (tag.length > MAX_TAG_ITEMS)
      throw new VerificationError('tag has too many elements')
    for (const elem of tag) {
      if (typeof elem !== 'string')
        throw new VerificationError('each tag element must be a string')
      if (textEncoder.encode(elem).length > MAX_TAG_STRING_BYTES)
        throw new VerificationError('tag string exceeds maximum length')
    }
  }

  const computedId = await computeId(event)
  if (computedId !== event.id)
    throw new VerificationError(`Event ID mismatch: expected ${computedId}, got ${event.id}`)

  const pubkey = event.type === 'genesis' ? event.group : event.author
  const bytes = canonicalSerialization(event)
  if (!verifySignature(pubkey, bytes, event.sig))
    throw new VerificationError('Invalid signature')
}
