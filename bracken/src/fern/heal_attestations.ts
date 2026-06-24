import { verifySignature } from './crypto'
import { computeSetHash, EMPTY_SET_HASH } from './completeness'
import { sortKeysDeep } from './events'
import { sha256Hex, isValidPubkey, isValidSig } from './utils'

export interface TrustedWitness {
  relay: string
  url: string
}

export interface Threshold {
  kind: string
  num: number
  den: number
  min: number
}

export interface HealChallenge {
  type: string
  group: string
  receiver: string
  ids_hash: string
  count: number
  trusted_witnesses: TrustedWitness[]
  threshold: Threshold
  nonce: string
  ts: number
  expires: number
  sig: string
}

export interface GroupHostAttestation {
  type: string
  group: string
  relay: string
  receiver: string
  challenge: string
  hosts: boolean
  ts: number
  expires: number
  sig: string
}

export interface InventoryAttestation {
  type: string
  group: string
  relay: string
  receiver: string
  challenge: string
  ids_hash: string
  count: number
  ts: number
  expires: number
  sig: string
}

export interface InventoryAttestationResult {
  attestation: InventoryAttestation | null
  covered: string[]
  missing: string[]
  inventoryMissing: boolean
}

export interface HealBatchResult {
  stored: string[]
  alreadyHave: string[]
  rejected: { id: string; reason: string }[]
}

const HEAL_CHALLENGE = 'heal_challenge'
const GROUP_HOST_ATTESTATION = 'group_host_attestation'
const INVENTORY_ATTESTATION = 'inventory_attestation'

function isHex(s: string, length: number): boolean {
  return new RegExp(`^[0-9a-f]{${length}}$`).test(s)
}

export function canonicalSerializationHealChallenge(c: HealChallenge): Uint8Array {
  const sortedWitnesses = [...c.trusted_witnesses]
    .sort((a, b) => a.relay.localeCompare(b.relay))
    .map((w) => ({ relay: w.relay, url: w.url }))
  const thresholdObj = { kind: c.threshold.kind, num: c.threshold.num, den: c.threshold.den, min: c.threshold.min }
  const array: unknown[] = [
    c.type,
    c.group,
    c.receiver,
    c.ids_hash,
    c.count,
    sortedWitnesses,
    thresholdObj,
    c.nonce,
    c.ts,
    c.expires,
  ]
  const sorted = sortKeysDeep(array) as unknown[]
  return new TextEncoder().encode(JSON.stringify(sorted))
}

export function canonicalSerializationGroupHostAttestation(a: GroupHostAttestation): Uint8Array {
  const array: unknown[] = [
    a.type,
    a.group,
    a.relay,
    a.receiver,
    a.challenge,
    a.hosts,
    a.ts,
    a.expires,
  ]
  const sorted = sortKeysDeep(array) as unknown[]
  return new TextEncoder().encode(JSON.stringify(sorted))
}

export function canonicalSerializationInventoryAttestation(a: InventoryAttestation): Uint8Array {
  const array: unknown[] = [
    a.type,
    a.group,
    a.relay,
    a.receiver,
    a.challenge,
    a.ids_hash,
    a.count,
    a.ts,
    a.expires,
  ]
  const sorted = sortKeysDeep(array) as unknown[]
  return new TextEncoder().encode(JSON.stringify(sorted))
}

export async function computeChallengeId(c: HealChallenge): Promise<string> {
  return sha256Hex(canonicalSerializationHealChallenge(c))
}

export async function verifyHealChallenge(
  c: HealChallenge,
  receiverPubkey?: string,
  nowTs?: number,
): Promise<boolean> {
  const now = nowTs ?? Math.floor(Date.now() / 1000)
  if (c.type !== HEAL_CHALLENGE) return false
  if (!isValidPubkey(c.group)) return false
  if (!isValidPubkey(c.receiver)) return false
  if (!isValidSig(c.sig)) return false
  if (c.ids_hash.length !== 64 || !isHex(c.ids_hash, 64)) return false
  if (!Number.isInteger(c.count) || c.count < 0) return false
  if (!Number.isInteger(c.ts) || c.ts <= 0) return false
  if (!Number.isInteger(c.expires) || c.expires <= 0) return false
  if (c.expires <= now) return false
  if (c.threshold.kind !== 'ratio') return false
  if (!Number.isInteger(c.threshold.num) || c.threshold.num <= 0) return false
  if (!Number.isInteger(c.threshold.den) || c.threshold.den <= 0) return false
  if (!Number.isInteger(c.threshold.min) || c.threshold.min <= 0) return false
  if (c.nonce.length === 0) return false
  if (c.count === 0 && c.ids_hash !== EMPTY_SET_HASH) return false

  if (receiverPubkey !== undefined && c.receiver !== receiverPubkey) return false

  if (c.trusted_witnesses.length > 0) {
    const witnessPubkeys = c.trusted_witnesses.map((w) => w.relay)
    const sortedPubkeys = [...witnessPubkeys].sort()
    if (JSON.stringify(witnessPubkeys) !== JSON.stringify(sortedPubkeys)) return false
    for (const w of c.trusted_witnesses) {
      if (!isValidPubkey(w.relay)) return false
      if (!w.url) return false
    }
  }

  const canonBytes = canonicalSerializationHealChallenge(c)
  return verifySignature(c.receiver, canonBytes, c.sig)
}

export async function verifyGroupHostAttestation(
  a: GroupHostAttestation,
  challengeId?: string,
  witnessPubkey?: string,
  nowTs?: number,
): Promise<boolean> {
  const now = nowTs ?? Math.floor(Date.now() / 1000)
  if (a.type !== GROUP_HOST_ATTESTATION) return false
  if (!isValidPubkey(a.group)) return false
  if (!isValidPubkey(a.relay)) return false
  if (!isValidPubkey(a.receiver)) return false
  if (!isValidSig(a.sig)) return false
  if (a.challenge.length !== 64 || !isHex(a.challenge, 64)) return false
  if (typeof a.hosts !== 'boolean') return false
  if (!Number.isInteger(a.ts) || a.ts <= 0) return false
  if (!Number.isInteger(a.expires) || a.expires <= 0) return false
  if (a.expires <= now) return false

  if (challengeId !== undefined && a.challenge !== challengeId) return false
  if (witnessPubkey !== undefined && a.relay !== witnessPubkey) return false

  const canonBytes = canonicalSerializationGroupHostAttestation(a)
  return verifySignature(a.relay, canonBytes, a.sig)
}

export async function verifyInventoryAttestation(
  a: InventoryAttestation,
  challengeId?: string,
  witnessPubkey?: string,
  nowTs?: number,
  coveredIds?: string[],
): Promise<boolean> {
  const now = nowTs ?? Math.floor(Date.now() / 1000)
  if (a.type !== INVENTORY_ATTESTATION) return false
  if (!isValidPubkey(a.group)) return false
  if (!isValidPubkey(a.relay)) return false
  if (!isValidPubkey(a.receiver)) return false
  if (!isValidSig(a.sig)) return false
  if (a.challenge.length !== 64 || !isHex(a.challenge, 64)) return false
  if (a.ids_hash.length !== 64 || !isHex(a.ids_hash, 64)) return false
  if (!Number.isInteger(a.count) || a.count < 0) return false
  if (!Number.isInteger(a.ts) || a.ts <= 0) return false
  if (!Number.isInteger(a.expires) || a.expires <= 0) return false
  if (a.expires <= now) return false

  if (challengeId !== undefined && a.challenge !== challengeId) return false
  if (witnessPubkey !== undefined && a.relay !== witnessPubkey) return false

  if (coveredIds !== undefined) {
    const hash = await computeSetHash(new Set(coveredIds))
    if (hash !== a.ids_hash) return false
    if (a.count !== coveredIds.length) return false
  }

  const canonBytes = canonicalSerializationInventoryAttestation(a)
  return verifySignature(a.relay, canonBytes, a.sig)
}

export function thresholdRequired(n: number, threshold: Threshold): number {
  if (n <= 0) return 1
  return Math.max(threshold.min, Math.ceil((threshold.num * n) / threshold.den))
}
