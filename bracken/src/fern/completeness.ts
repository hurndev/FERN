import { verifySignature } from './crypto'
import type { GroupStatus } from './relay'
import { isValidEventId, isValidPubkey, isValidSig, sha256Hex } from './utils'

export const EMPTY_SET_HASH =
  'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'

export async function computeSetHash(ids: Set<string>): Promise<string> {
  if (ids.size === 0) return EMPTY_SET_HASH
  return sha256Hex(new TextEncoder().encode([...ids].sort().join('\n')))
}

export function canonicalSerializationGroupStatus(att: GroupStatus): Uint8Array {
  const arr = [
    att.group,
    att.relay,
    att.set_hash,
    [...att.tips].sort(),
    att.count,
    att.prev,
    att.ts,
  ]
  return new TextEncoder().encode(JSON.stringify(arr))
}

export function verifyGroupStatus(att: GroupStatus): boolean {
  if (!isValidPubkey(att.group)) return false
  if (!isValidPubkey(att.relay)) return false
  if (!isValidEventId(att.set_hash)) return false
  if (!isValidSig(att.sig)) return false
  if (!Number.isInteger(att.ts) || att.ts <= 0) return false
  if (!Number.isInteger(att.count) || att.count < 0) return false
  if (att.prev !== null && !isValidEventId(att.prev)) return false
  if (!Array.isArray(att.tips) || att.tips.some((tip) => !isValidEventId(tip))) return false
  const sortedTips = [...att.tips].sort()
  if (JSON.stringify(att.tips) !== JSON.stringify(sortedTips)) return false
  return verifySignature(att.relay, canonicalSerializationGroupStatus(att), att.sig)
}

export async function hashGroupStatus(att: GroupStatus): Promise<string> {
  return sha256Hex(canonicalSerializationGroupStatus(att))
}
