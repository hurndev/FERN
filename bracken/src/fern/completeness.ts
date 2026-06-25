import { verifySignature } from './crypto'
import { log } from './logger'
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
  if (!isValidPubkey(att.group)) {
    log.eventVerifyFailed('group_status', att.set_hash, 'invalid group pubkey')
    return false
  }
  if (!isValidPubkey(att.relay)) {
    log.eventVerifyFailed('group_status', att.set_hash, 'invalid relay pubkey')
    return false
  }
  if (!isValidEventId(att.set_hash)) {
    log.eventVerifyFailed('group_status', att.set_hash, 'invalid set_hash format')
    return false
  }
  if (!isValidSig(att.sig)) {
    log.eventVerifyFailed('group_status', att.set_hash, 'invalid signature format')
    return false
  }
  if (!Number.isInteger(att.ts) || att.ts <= 0) {
    log.eventVerifyFailed('group_status', att.set_hash, 'invalid timestamp')
    return false
  }
  if (!Number.isInteger(att.count) || att.count < 0) {
    log.eventVerifyFailed('group_status', att.set_hash, 'invalid count')
    return false
  }
  if (att.prev !== null && !isValidEventId(att.prev)) {
    log.eventVerifyFailed('group_status', att.set_hash, 'invalid prev hash')
    return false
  }
  if (!Array.isArray(att.tips) || att.tips.some((tip) => !isValidEventId(tip))) {
    log.eventVerifyFailed('group_status', att.set_hash, 'invalid tips')
    return false
  }
  const sortedTips = [...att.tips].sort()
  if (JSON.stringify(att.tips) !== JSON.stringify(sortedTips)) {
    log.eventVerifyFailed('group_status', att.set_hash, 'tips not sorted')
    return false
  }
  const valid = verifySignature(att.relay, canonicalSerializationGroupStatus(att), att.sig)
  if (!valid) {
    log.eventVerifyFailed('group_status', att.set_hash, 'signature verification failed')
  }
  return valid
}

export async function hashGroupStatus(att: GroupStatus): Promise<string> {
  return sha256Hex(canonicalSerializationGroupStatus(att))
}
