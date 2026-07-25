import { verifySignature } from './crypto'
import type { FernEvent } from './events'
import { canonicalJson, verifyEvent } from './events'
import { isValidEventId, isValidPubkey, isValidSig, sha256Hex } from './utils'

export interface Validator {
  pubkey: string
  url: string
  operator: string
}

export interface ValidatorSet {
  epoch: number
  fault_tolerance: number
  validators: Validator[]
}

export interface TimestampObservation {
  type: 'timestamp_observation'
  group: string
  chain_id: string
  epoch: number
  height: number
  round: number
  candidate_id: string
  validator: string
  observed_ms: number[]
  sig: string
}

export interface Candidate {
  type: 'candidate'
  id: string
  group: string
  chain_id: string
  epoch: number
  height: number
  round: number
  previous_block_hash: string
  previous_state_root: string
  events: FernEvent[]
  governance: FernEvent | null
  proposer: string
  sig: string
}

export interface Block {
  type: 'block'
  id: string
  group: string
  chain_id: string
  epoch: number
  height: number
  candidate: Candidate
  observations: TimestampObservation[]
  certified_times_ms: number[]
  previous_history_root: string
  state_root: string
  history_root: string
}

export interface Vote {
  type: 'vote'
  group: string
  chain_id: string
  epoch: number
  height: number
  round: number
  phase: 'prevote' | 'precommit'
  block_id: string | null
  validator: string
  sig: string
}

export interface Commit {
  type: 'commit'
  group: string
  chain_id: string
  epoch: number
  height: number
  round: number
  block: Block
  precommits: Vote[]
}

export interface SyncReady {
  type: 'sync_ready'
  group: string
  chain_id: string
  from_epoch: number
  to_epoch: number
  validator: string
  checkpoint_height: number
  checkpoint_block_hash: string
  history_root: string
  byte_count: number
  sig: string
}

export const GOVERNANCE_TYPES = new Set([
  'invite', 'kick', 'ban', 'unban', 'admin_add', 'admin_remove',
  'validator_update', 'metadata_update', 'chat.channel_create',
  'chat.channel_update', 'chat.channel_delete', 'chat.settings_update',
])

const MAX_VALIDATORS = 100
const MAX_BLOCK_EVENTS = 500
const MAX_BLOCK_BYTES = 2 * 1024 * 1024
const STANDARD_BFT_MIN_VALIDATORS = 4

export function validatorQuorum(set: ValidatorSet): number {
  if (set.validators.length < STANDARD_BFT_MIN_VALIDATORS) return set.validators.length
  return Math.floor((2 * set.validators.length) / 3) + 1
}

export function isSmallUnanimousValidatorSet(set: ValidatorSet): boolean {
  return set.validators.length < STANDARD_BFT_MIN_VALIDATORS
}

export function validateValidatorSet(set: ValidatorSet): void {
  if (!Number.isInteger(set.epoch) || set.epoch < 0) throw new Error('invalid validator epoch')
  if (!Number.isInteger(set.fault_tolerance) || set.fault_tolerance < 0)
    throw new Error('invalid fault tolerance')
  if (set.validators.length === 0) throw new Error('validator set cannot be empty')
  // floor((n-1)/3) yields 0 for fewer than 4 validators (unanimous mode).
  const expectedF = Math.floor((set.validators.length - 1) / 3)
  if (set.fault_tolerance !== expectedF)
    throw new Error(
      `${set.validators.length} validators require fault_tolerance=${expectedF}, ` +
      `not ${set.fault_tolerance}`,
    )
  if (set.validators.length > MAX_VALIDATORS) throw new Error('too many validators')
  const keys = set.validators.map((validator) => validator.pubkey)
  if (new Set(keys).size !== keys.length || keys.some((key) => !isValidPubkey(key)))
    throw new Error('invalid or duplicate validator key')
  if (keys.join(',') !== [...keys].sort().join(','))
    throw new Error('validators are not sorted by pubkey')
  const urls = set.validators.map((validator) => validator.url)
  if (new Set(urls).size !== urls.length) throw new Error('duplicate validator URL')
  for (const validator of set.validators) {
    let parsed: URL
    try { parsed = new URL(validator.url) } catch { throw new Error('invalid validator URL') }
    if (!['ws:', 'wss:'].includes(parsed.protocol) || !parsed.host)
      throw new Error('validator URL must use ws:// or wss://')
    if (new TextEncoder().encode(validator.url).length > 2048 ||
      typeof validator.operator !== 'string' || new TextEncoder().encode(validator.operator).length > 200)
      throw new Error('invalid validator metadata')
  }
}

export function proposerFor(set: ValidatorSet, height: number, round: number): Validator {
  if (!Number.isSafeInteger(height) || height <= 0 || !Number.isSafeInteger(round) || round < 0)
    throw new Error('invalid consensus position')
  return set.validators[(height + round - 1) % set.validators.length]
}

function candidatePayload(candidate: Candidate): unknown[] {
  return [
    'candidate', candidate.group, candidate.chain_id, candidate.epoch,
    candidate.height, candidate.round, candidate.previous_block_hash,
    candidate.previous_state_root, candidate.events.map((event) => event.id),
    candidate.governance?.id ?? null, candidate.proposer,
  ]
}

export async function computeCandidateId(candidate: Candidate): Promise<string> {
  return sha256Hex(canonicalJson(candidatePayload(candidate)))
}

function observationPayload(observation: TimestampObservation): unknown[] {
  return [
    'timestamp_observation', observation.group, observation.chain_id,
    observation.epoch, observation.height, observation.round,
    observation.candidate_id, observation.validator, observation.observed_ms,
  ]
}

function votePayload(vote: Vote): unknown[] {
  return [
    'vote', vote.group, vote.chain_id, vote.epoch, vote.height, vote.round,
    vote.phase, vote.block_id, vote.validator,
  ]
}

function blockPayload(block: Block): unknown[] {
  return [
    'block', block.candidate, block.observations, block.certified_times_ms,
    block.previous_history_root, block.state_root, block.history_root,
  ]
}

export async function computeBlockId(block: Block): Promise<string> {
  return sha256Hex(canonicalJson(blockPayload(block)))
}

export async function computeHistoryRoot(previous: string, ids: string[]): Promise<string> {
  return sha256Hex(canonicalJson(['fern-bft-history', previous, ids]))
}

function medians(observations: TimestampObservation[], eventCount: number): number[] {
  return Array.from({ length: eventCount }, (_, index) => {
    const values = observations.map((observation) => observation.observed_ms[index]).sort((a, b) => a - b)
    return values[Math.floor((values.length - 1) / 2)]
  })
}

export async function verifyCommitEvidence(commit: Commit, set: ValidatorSet): Promise<void> {
  validateValidatorSet(set)
  const block = commit.block
  const candidate = block.candidate
  if (!Number.isSafeInteger(commit.round) || commit.round < 0)
    throw new Error('invalid commit round')
  if (commit.group !== block.group || commit.chain_id !== block.chain_id ||
    commit.height !== block.height || commit.epoch !== block.epoch)
    throw new Error('commit envelope mismatch')
  if (candidate.group !== block.group || candidate.chain_id !== block.chain_id ||
    block.group !== commit.group || block.chain_id !== commit.chain_id)
    throw new Error('candidate chain mismatch')
  if (candidate.epoch !== set.epoch || candidate.height !== block.height)
    throw new Error('candidate consensus position mismatch')
  if (!isValidPubkey(block.group) || !isValidEventId(block.chain_id) ||
    !isValidEventId(candidate.previous_block_hash) || !isValidEventId(candidate.previous_state_root) ||
    !Number.isSafeInteger(candidate.height) || candidate.height <= 0 ||
    !Number.isSafeInteger(candidate.round) || candidate.round < 0)
    throw new Error('invalid candidate fields')
  if (candidate.proposer !== proposerFor(set, candidate.height, candidate.round).pubkey)
    throw new Error('wrong candidate proposer')
  if (!isValidSig(candidate.sig) || !verifySignature(candidate.proposer, canonicalJson(candidatePayload(candidate)), candidate.sig))
    throw new Error('invalid candidate signature')
  if (!isValidEventId(candidate.id) || candidate.id !== await computeCandidateId(candidate))
    throw new Error('candidate id mismatch')
  const allEvents = [...candidate.events, ...(candidate.governance ? [candidate.governance] : [])]
  if (allEvents.length === 0 || allEvents.length > MAX_BLOCK_EVENTS)
    throw new Error('invalid event count in block')
  if (new Set(allEvents.map((event) => event.id)).size !== allEvents.length)
    throw new Error('duplicate event in block')
  if (candidate.events.some((event) => GOVERNANCE_TYPES.has(event.type)) ||
    (candidate.governance !== null && !GOVERNANCE_TYPES.has(candidate.governance.type)))
    throw new Error('invalid governance slot')
  for (const event of allEvents) {
    await verifyEvent(event)
    if (event.group !== block.group || event.type === 'genesis')
      throw new Error('block contains an event from another group')
  }

  const quorum = validatorQuorum(set)
  if (block.observations.length !== quorum) throw new Error('wrong observation quorum size')
  const validatorKeys = new Set(set.validators.map((validator) => validator.pubkey))
  const observers = new Set<string>()
  for (const observation of block.observations) {
    if (observers.has(observation.validator) || !validatorKeys.has(observation.validator))
      throw new Error('duplicate or inactive timestamp observer')
    observers.add(observation.validator)
    if (
      observation.group !== block.group || observation.chain_id !== block.chain_id ||
      observation.epoch !== block.epoch || observation.height !== block.height ||
      observation.round !== candidate.round || observation.candidate_id !== candidate.id ||
      observation.observed_ms.length !== allEvents.length ||
      observation.observed_ms.some((value) => !Number.isSafeInteger(value) || value <= 0) ||
      !isValidSig(observation.sig) ||
      !verifySignature(observation.validator, canonicalJson(observationPayload(observation)), observation.sig)
    ) throw new Error('invalid timestamp observation')
  }
  const sortedObservers = [...block.observations].sort((a, b) => a.validator.localeCompare(b.validator))
  if (sortedObservers.map((value) => value.validator).join(',') !== block.observations.map((value) => value.validator).join(','))
    throw new Error('observation bundle is not sorted')
  if (medians(block.observations, allEvents.length).join(',') !== block.certified_times_ms.join(','))
    throw new Error('certified timestamp median mismatch')
  if (block.certified_times_ms.some((value) => !Number.isSafeInteger(value) || value <= 0) ||
    !isValidEventId(block.previous_history_root) || !isValidEventId(block.state_root) ||
    !isValidEventId(block.history_root)) throw new Error('invalid block roots or timestamps')
  if (block.history_root !== await computeHistoryRoot(block.previous_history_root, allEvents.map((event) => event.id)))
    throw new Error('history root mismatch')
  if (!isValidEventId(block.id) || block.id !== await computeBlockId(block))
    throw new Error('block id mismatch')
  if (canonicalJson(block).length > MAX_BLOCK_BYTES) throw new Error('block exceeds size limit')

  const voters = new Set<string>()
  if (commit.precommits.length > set.validators.length)
    throw new Error('too many commit precommits')
  for (const vote of commit.precommits) {
    if (
      vote.phase !== 'precommit' || vote.block_id !== block.id || vote.group !== block.group ||
      vote.chain_id !== block.chain_id || vote.epoch !== block.epoch ||
      vote.height !== block.height || vote.round !== commit.round ||
      !validatorKeys.has(vote.validator) || voters.has(vote.validator) || !isValidSig(vote.sig) ||
      !verifySignature(vote.validator, canonicalJson(votePayload(vote)), vote.sig)
    ) throw new Error('invalid commit precommit')
    voters.add(vote.validator)
  }
  if (voters.size < quorum) throw new Error('insufficient commit precommits')
}

export function verifyValidatorTransition(
  event: FernEvent,
  current: ValidatorSet,
  group: string,
  chainId: string,
  checkpointHeight: number,
  checkpointBlockHash: string,
  historyRoot: string,
  logicalBytes: number,
): ValidatorSet {
  if (event.type !== 'validator_update') return current
  const next: ValidatorSet = {
    epoch: current.epoch + 1,
    fault_tolerance: Number(event.content['fault_tolerance']),
    validators: event.content['validators'] as Validator[],
  }
  validateValidatorSet(next)
  const currentKeys = new Set(current.validators.map((validator) => validator.pubkey))
  const added = new Set(next.validators.map((validator) => validator.pubkey).filter((key) => !currentKeys.has(key)))
  const readiness = Array.isArray(event.content['readiness'])
    ? event.content['readiness'] as SyncReady[] : []
  const seen = new Set<string>()
  for (const ready of readiness) {
    const payload = [
      'sync_ready', ready.group, ready.chain_id, ready.from_epoch, ready.to_epoch,
      ready.validator, ready.checkpoint_height, ready.checkpoint_block_hash,
      ready.history_root, ready.byte_count,
    ]
    if (
      !added.has(ready.validator) || seen.has(ready.validator) ||
      ready.group !== group || ready.chain_id !== chainId ||
      ready.from_epoch !== current.epoch || ready.to_epoch !== next.epoch ||
      ready.checkpoint_height !== checkpointHeight ||
      ready.checkpoint_block_hash !== checkpointBlockHash || ready.history_root !== historyRoot ||
      ready.byte_count !== logicalBytes || !Number.isSafeInteger(ready.byte_count) || ready.byte_count < 0 ||
      !isValidSig(ready.sig) ||
      !verifySignature(ready.validator, canonicalJson(payload), ready.sig)
    ) throw new Error('invalid validator readiness proof')
    seen.add(ready.validator)
  }
  if (seen.size !== added.size) throw new Error('readiness does not cover new validators')
  return next
}
