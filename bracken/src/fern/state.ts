import type { ValidatorSet } from './bft'
import { GOVERNANCE_TYPES, validateValidatorSet } from './bft'
import type { FernEvent } from './events'
import { canonicalJson } from './events'
import { sha256Hex } from './utils'
import { isValidEventId, isValidPubkey } from './utils'

export interface BanEntry {
  until: number | null
  reason: string
}

export interface Channel {
  id: string
  name: string
  description: string
  position: number
}

export interface GroupState {
  group: string
  chainId: string
  validatorSet: ValidatorSet
  members: Set<string>
  joined: Set<string>
  banned: Map<string, BanEntry>
  admins: Set<string>
  sequences: Map<string, number>
  metadata: { name: string; description: string }
  public: boolean
  app: string
  channels: Map<string, Channel>
  chatSettings: { default_channel?: string; system_channel?: string }
}

const encoder = new TextEncoder()

function record(value: unknown, field: string): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value))
    throw new Error(`${field} must be an object`)
  return value as Record<string, unknown>
}

function boundedString(value: unknown, field: string, maximum: number, minimum = 0): string {
  if (typeof value !== 'string') throw new Error(`${field} must be a string`)
  const length = encoder.encode(value).length
  if (length < minimum || length > maximum) throw new Error(`${field} has invalid length`)
  return value
}

function exactKeys(value: Record<string, unknown>, allowed: string[]): void {
  const extra = Object.keys(value).find((key) => !allowed.includes(key))
  if (extra) throw new Error(`unexpected content field: ${extra}`)
}

function pubkey(value: unknown, field: string): string {
  if (typeof value !== 'string' || !isValidPubkey(value)) throw new Error(`${field} is not a public key`)
  return value
}

function eventId(value: unknown, field: string): string {
  if (typeof value !== 'string' || !isValidEventId(value)) throw new Error(`${field} is not an event ID`)
  return value
}

function integer(value: unknown, field: string): number {
  if (!Number.isSafeInteger(value)) throw new Error(`${field} must be a safe integer`)
  return value as number
}

function validateGenesis(genesis: FernEvent): void {
  const content = genesis.content
  const required = ['chain_id', 'name', 'description', 'public', 'founder', 'admins', 'validators', 'fault_tolerance', 'app']
  for (const key of required) if (!(key in content)) throw new Error(`missing genesis field: ${key}`)
  for (const key of Object.keys(content)) {
    if (!key.includes('.') && !required.includes(key)) throw new Error(`unexpected genesis field: ${key}`)
  }
  eventId(content['chain_id'], 'chain_id')
  boundedString(content['name'], 'name', 100, 1)
  boundedString(content['description'], 'description', 2000)
  if (typeof content['public'] !== 'boolean') throw new Error('public must be boolean')
  if (pubkey(content['founder'], 'founder') !== genesis.author) throw new Error('founder must equal author')
  if (!Array.isArray(content['admins']) || content['admins'].length < 1 || content['admins'].length > 256)
    throw new Error('invalid genesis admins')
  const admins = content['admins'].map((value) => pubkey(value, 'admin'))
  if (!admins.includes(genesis.author)) throw new Error('founder must be an admin')
  boundedString(content['app'], 'app', 64, 1)
  if (!Array.isArray(content['validators'])) throw new Error('validators must be an array')
  validateValidatorSet({
    epoch: 0,
    fault_tolerance: integer(content['fault_tolerance'], 'fault_tolerance'),
    validators: content['validators'] as ValidatorSet['validators'],
  })
  if (content['app'] === 'chat') {
    if (!Array.isArray(content['chat.channels']) || content['chat.channels'].length === 0)
      throw new Error('chat genesis needs a channel')
    const ids = new Set<string>()
    const names = new Set<string>()
    for (const raw of content['chat.channels']) {
      const channel = record(raw, 'channel')
      exactKeys(channel, ['id', 'name', 'description', 'position'])
      const id = eventId(channel['id'], 'channel.id')
      if (ids.has(id)) throw new Error('duplicate channel ID')
      ids.add(id)
      const name = boundedString(channel['name'], 'channel.name', 80, 1)
      if (names.has(name)) throw new Error('duplicate channel name')
      names.add(name)
      if ('description' in channel) boundedString(channel['description'], 'channel.description', 500)
      if ('position' in channel) integer(channel['position'], 'channel.position')
    }
    for (const key of ['chat.default_channel', 'chat.system_channel']) {
      if (key in content && !ids.has(eventId(content[key], key)))
        throw new Error(`${key} references an unknown channel`)
    }
  }
}

function initialState(genesis: FernEvent): GroupState {
  validateGenesis(genesis)
  const content = genesis.content
  const validators = content['validators'] as ValidatorSet['validators']
  const validatorSet: ValidatorSet = {
    epoch: 0,
    fault_tolerance: Number(content['fault_tolerance']),
    validators,
  }
  validateValidatorSet(validatorSet)
  const channels = new Map<string, Channel>()
  const rawChannels = content['chat.channels']
  if (Array.isArray(rawChannels)) {
    rawChannels.forEach((raw, index) => {
      const value = raw as Record<string, unknown>
      const id = String(value['id'] ?? '')
      channels.set(id, {
        id,
        name: String(value['name'] ?? id),
        description: String(value['description'] ?? ''),
        position: typeof value['position'] === 'number' ? value['position'] : index,
      })
    })
  }
  const first = channels.keys().next().value ?? ''
  const founder = String(content['founder'])
  return {
    group: genesis.group,
    chainId: String(content['chain_id']),
    validatorSet,
    members: new Set([founder]),
    joined: new Set([founder]),
    banned: new Map(),
    admins: new Set(content['admins'] as string[]),
    sequences: new Map(),
    metadata: {
      name: String(content['name'] ?? ''),
      description: String(content['description'] ?? ''),
    },
    public: content['public'] as boolean,
    app: String(content['app'] ?? ''),
    channels,
    chatSettings: channels.size > 0 ? {
      default_channel: String(content['chat.default_channel'] ?? first),
      system_channel: String(content['chat.system_channel'] ?? first),
    } : {},
  }
}

function bannedAt(state: GroupState, pubkey: string, certifiedSeconds: number): boolean {
  const entry = state.banned.get(pubkey)
  return entry !== undefined && (entry.until === null || entry.until > certifiedSeconds)
}

function authorised(state: GroupState, event: FernEvent, certifiedSeconds: number): boolean {
  if (event.type === 'join') {
    return (state.public || state.members.has(event.author)) && !bannedAt(state, event.author, certifiedSeconds)
  }
  if (event.type === 'leave') return true
  if (GOVERNANCE_TYPES.has(event.type)) return state.admins.has(event.author)
  if (event.type.includes('.')) return event.type.split('.', 1)[0] === state.app &&
    state.joined.has(event.author) && !bannedAt(state, event.author, certifiedSeconds)
  return false
}

function validateForState(state: GroupState, event: FernEvent): void {
  if (event.group !== state.group) throw new Error('event belongs to another group')
  const content = event.content
  const type = event.type
  if (type === 'join' || type === 'leave') exactKeys(content, [])
  else if (type === 'invite') {
    exactKeys(content, ['invitee', 'role'])
    pubkey(content['invitee'], 'invitee')
    if (content['role'] !== 'member') throw new Error('invite role must be member')
  } else if (['kick', 'unban', 'admin_add', 'admin_remove'].includes(type)) {
    exactKeys(content, ['target']); pubkey(content['target'], 'target')
  } else if (type === 'ban') {
    exactKeys(content, ['target', 'until', 'reason']); pubkey(content['target'], 'target')
    if (content['until'] !== null && content['until'] !== undefined && integer(content['until'], 'until') <= 0)
      throw new Error('ban expiry must be positive')
    boundedString(content['reason'] ?? '', 'reason', 500)
  } else if (type === 'metadata_update') {
    exactKeys(content, ['name', 'description'])
    if (!('name' in content) && !('description' in content)) throw new Error('empty metadata update')
    if ('name' in content) boundedString(content['name'], 'name', 100, 1)
    if ('description' in content) boundedString(content['description'], 'description', 2000)
  } else if (type === 'validator_update') {
    exactKeys(content, ['validators', 'fault_tolerance', 'readiness'])
    if (!Array.isArray(content['validators']) || !Array.isArray(content['readiness']))
      throw new Error('invalid validator update arrays')
    validateValidatorSet({
      epoch: state.validatorSet.epoch + 1,
      fault_tolerance: integer(content['fault_tolerance'], 'fault_tolerance'),
      validators: content['validators'] as ValidatorSet['validators'],
    })
  } else if (type === 'chat.message') {
    exactKeys(content, ['text', 'channel', 'reply_to'])
    boundedString(content['text'], 'text', 16_000, 1)
    const channel = eventId(content['channel'], 'channel')
    if (!state.channels.has(channel)) throw new Error('message channel does not exist')
    if (content['reply_to'] !== null && content['reply_to'] !== undefined) eventId(content['reply_to'], 'reply_to')
  } else if (type === 'chat.reaction') {
    exactKeys(content, ['target', 'emoji']); eventId(content['target'], 'target')
    boundedString(content['emoji'], 'emoji', 64, 1)
  } else if (type === 'chat.nickname_set') {
    exactKeys(content, ['nickname']); boundedString(content['nickname'], 'nickname', 80, 1)
  } else if (type === 'chat.channel_create') {
    exactKeys(content, ['id', 'name', 'description', 'position'])
    const id = eventId(content['id'], 'id')
    if (state.channels.has(id)) throw new Error('channel ID already exists')
    const name = boundedString(content['name'], 'name', 80, 1)
    if ([...state.channels.values()].some((channel) => channel.name === name))
      throw new Error('channel name already exists')
    if ('description' in content) boundedString(content['description'], 'description', 500)
    if ('position' in content) integer(content['position'], 'position')
  } else if (type === 'chat.channel_update') {
    exactKeys(content, ['id', 'name', 'description', 'position'])
    const id = eventId(content['id'], 'id')
    if (!state.channels.has(id)) throw new Error('channel does not exist')
    if (!['name', 'description', 'position'].some((key) => key in content)) throw new Error('empty channel update')
    if ('name' in content) boundedString(content['name'], 'name', 80, 1)
    if ('description' in content) boundedString(content['description'], 'description', 500)
    if ('position' in content) integer(content['position'], 'position')
  } else if (type === 'chat.channel_delete') {
    exactKeys(content, ['id', 'name'])
    const id = eventId(content['id'], 'id')
    if (!state.channels.has(id) || state.chatSettings.default_channel === id)
      throw new Error('cannot delete this channel')
    if ('name' in content) boundedString(content['name'], 'name', 80, 1)
  } else if (type === 'chat.settings_update') {
    exactKeys(content, ['default_channel', 'system_channel'])
    if (!('default_channel' in content) && !('system_channel' in content)) throw new Error('empty settings update')
    for (const key of ['default_channel', 'system_channel']) {
      if (key in content && !state.channels.has(eventId(content[key], key)))
        throw new Error('chat setting references an unknown channel')
    }
  } else if (!type.includes('.') || type.startsWith('chat.') || type.split('.', 1)[0] !== state.app) {
    throw new Error(`unknown event type: ${type}`)
  }
}

function apply(state: GroupState, event: FernEvent): void {
  const content = event.content
  switch (event.type) {
    case 'invite': state.members.add(String(content['invitee'])); break
    case 'join': state.members.add(event.author); state.joined.add(event.author); break
    case 'leave': state.joined.delete(event.author); break
    case 'kick':
      state.joined.delete(String(content['target']))
      state.admins.delete(String(content['target']))
      break
    case 'ban':
      state.banned.set(String(content['target']), {
        until: content['until'] === null || content['until'] === undefined ? null : Number(content['until']),
        reason: String(content['reason'] ?? ''),
      })
      state.joined.delete(String(content['target']))
      state.admins.delete(String(content['target']))
      break
    case 'unban': state.banned.delete(String(content['target'])); break
    case 'admin_add': state.admins.add(String(content['target'])); break
    case 'admin_remove': state.admins.delete(String(content['target'])); break
    case 'metadata_update':
      if ('name' in content) state.metadata.name = String(content['name'])
      if ('description' in content) state.metadata.description = String(content['description'])
      break
    case 'validator_update': {
      const validators = content['validators'] as ValidatorSet['validators']
      state.validatorSet = {
        epoch: state.validatorSet.epoch + 1,
        fault_tolerance: Number(content['fault_tolerance']),
        validators,
      }
      validateValidatorSet(state.validatorSet)
      break
    }
    case 'chat.channel_create': {
      const id = String(content['id'])
      state.channels.set(id, {
        id,
        name: String(content['name']),
        description: String(content['description'] ?? ''),
        position: Number(content['position'] ?? state.channels.size),
      })
      break
    }
    case 'chat.channel_update': {
      const id = String(content['id'])
      const current = state.channels.get(id)
      if (current) state.channels.set(id, {
        id,
        name: String(content['name'] ?? current.name),
        description: String(content['description'] ?? current.description),
        position: Number(content['position'] ?? current.position),
      })
      break
    }
    case 'chat.channel_delete': {
      const id = String(content['id'])
      state.channels.delete(id)
      if (state.chatSettings.system_channel === id)
        state.chatSettings.system_channel = state.channels.keys().next().value ?? ''
      break
    }
    case 'chat.settings_update':
      if ('default_channel' in content) state.chatSettings.default_channel = String(content['default_channel'])
      if ('system_channel' in content) state.chatSettings.system_channel = String(content['system_channel'])
      break
  }
  state.sequences.set(event.author, event.seq)
}

export function deriveGroupState(events: FernEvent[]): {
  state: GroupState | null
  rejected: FernEvent[]
  acceptedIds: Set<string>
  genesis: FernEvent | null
} {
  const genesis = events.find((event) => event.type === 'genesis') ?? null
  if (!genesis) return { state: null, rejected: [], acceptedIds: new Set(), genesis: null }
  const state = initialState(genesis)
  const rejected: FernEvent[] = []
  const acceptedIds = new Set<string>([genesis.id])
  const finalized = events
    .filter((event) => event.type !== 'genesis' && event.bft?.status === 'finalized')
    .sort((a, b) => (a.bft?.height ?? 0) - (b.bft?.height ?? 0) || (a.bft?.position ?? 0) - (b.bft?.position ?? 0))
  for (const event of finalized) {
    const expected = (state.sequences.get(event.author) ?? 0) + 1
    const certifiedSeconds = Math.floor((event.bft?.certifiedTimeMs ?? 0) / 1000)
    try {
      validateForState(state, event)
    } catch {
      rejected.push(event)
      continue
    }
    if (event.seq !== expected || !authorised(state, event, certifiedSeconds)) {
      rejected.push(event)
      continue
    }
    apply(state, event)
    acceptedIds.add(event.id)
  }
  const pendingSequences = new Map(state.sequences)
  const pending = events
    .filter((event) => event.bft?.status === 'pending')
    .sort((left, right) => left.author.localeCompare(right.author) || left.seq - right.seq)
  for (const event of pending) {
    const expected = (pendingSequences.get(event.author) ?? 0) + 1
    try {
      validateForState(state, event)
      if (event.seq !== expected || !authorised(state, event, Math.floor(Date.now() / 1000))) continue
    } catch {
      continue
    }
    pendingSequences.set(event.author, event.seq)
    acceptedIds.add(event.id)
  }
  return { state, rejected, acceptedIds, genesis }
}

export async function computeStateRoot(state: GroupState): Promise<string> {
  const banned: Record<string, unknown> = {}
  for (const key of [...state.banned.keys()].sort()) banned[key] = state.banned.get(key)
  const sequences: Record<string, number> = {}
  for (const key of [...state.sequences.keys()].sort()) sequences[key] = state.sequences.get(key)!
  const channels: Record<string, unknown> = {}
  for (const key of [...state.channels.keys()].sort()) channels[key] = state.channels.get(key)
  const value = {
    protocol: 'fern-bft-1',
    group: state.group,
    chain_id: state.chainId,
    validator_set: state.validatorSet,
    members: [...state.members].sort(),
    joined: [...state.joined].sort(),
    banned,
    admins: [...state.admins].sort(),
    sequences,
    metadata: state.metadata,
    public: state.public,
    app: state.app,
    channels,
    chat_settings: state.chatSettings,
  }
  return sha256Hex(canonicalJson(['fern-bft-state', value]))
}

export { bannedAt as isBannedAt, authorised as isAuthorised }
