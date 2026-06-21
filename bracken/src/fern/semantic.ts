import type { FernEvent } from './events'
import {
  MAX_ADMINS,
  MAX_APP_NAME_BYTES,
  MAX_BAN_REASON_BYTES,
  MAX_CHANNEL_DESCRIPTION_BYTES,
  MAX_CHANNEL_ID_BYTES,
  MAX_CHANNEL_NAME_BYTES,
  MAX_GROUP_DESCRIPTION_BYTES,
  MAX_GROUP_NAME_BYTES,
  MAX_MESSAGE_TEXT_BYTES,
  MAX_NICKNAME_BYTES,
  MAX_REACTION_BYTES,
  MAX_RELAYS,
  MAX_RELAY_URL_BYTES,
} from './limits'
import { isValidEventId, isValidPubkey } from './utils'

export class SemanticValidationError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'SemanticValidationError'
  }
}

const textEncoder = new TextEncoder()

function byteLength(value: string): number {
  return textEncoder.encode(value).length
}

function stringField(
  value: unknown,
  field: string,
  { min = 0, max }: { min?: number; max: number },
): string {
  if (typeof value !== 'string') throw new SemanticValidationError(`${field} must be a string`)
  const size = byteLength(value)
  if (size < min) throw new SemanticValidationError(`${field} is too short`)
  if (size > max) throw new SemanticValidationError(`${field} exceeds maximum length`)
  return value
}

function pubkeyField(value: unknown, field: string): string {
  if (typeof value !== 'string' || !isValidPubkey(value)) {
    throw new SemanticValidationError(`${field} must be a pubkey`)
  }
  return value
}

function eventIdField(value: unknown, field: string): string {
  if (typeof value !== 'string' || !isValidEventId(value)) {
    throw new SemanticValidationError(`${field} must be an event id`)
  }
  return value
}

function integerField(value: unknown, field: string): number {
  if (typeof value !== 'number' || !Number.isInteger(value)) {
    throw new SemanticValidationError(`${field} must be an integer`)
  }
  return value
}

function only(content: Record<string, unknown>, allowed: string[]): void {
  const allowedSet = new Set(allowed)
  const extra = Object.keys(content).find((key) => !allowedSet.has(key))
  if (extra) throw new SemanticValidationError(`unexpected content field: ${extra}`)
}

function relayUrl(value: unknown): string {
  const url = stringField(value, 'relay', { min: 1, max: MAX_RELAY_URL_BYTES })
  let parsed: URL
  try {
    parsed = new URL(url)
  } catch {
    throw new SemanticValidationError('relay must be a ws:// or wss:// URL')
  }
  if (parsed.protocol !== 'ws:' && parsed.protocol !== 'wss:') {
    throw new SemanticValidationError('relay must be a ws:// or wss:// URL')
  }
  return url
}

function channelId(value: unknown, field = 'channel id'): string {
  return stringField(value, field, { min: 1, max: MAX_CHANNEL_ID_BYTES })
}

function validateChatChannel(raw: unknown): string {
  if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
    throw new SemanticValidationError('chat channel must be an object')
  }
  const record = raw as Record<string, unknown>
  only(record, ['id', 'name', 'description', 'position'])
  const id = channelId(record['id'], 'channel.id')
  stringField(record['name'], 'channel.name', { min: 1, max: MAX_CHANNEL_NAME_BYTES })
  if ('description' in record) {
    stringField(record['description'], 'channel.description', { max: MAX_CHANNEL_DESCRIPTION_BYTES })
  }
  if ('position' in record) integerField(record['position'], 'channel.position')
  return id
}

export function validateEventSemantics(event: FernEvent): void {
  const c = event.content
  switch (event.type) {
    case 'genesis': {
      const required = ['name', 'description', 'public', 'founder', 'admins', 'relays', 'app']
      for (const field of required) {
        if (!(field in c)) throw new SemanticValidationError(`missing genesis field: ${field}`)
      }
      for (const key of Object.keys(c)) {
        if (!key.includes('.') && !required.includes(key)) {
          throw new SemanticValidationError(`unexpected genesis protocol field: ${key}`)
        }
      }
      stringField(c['name'], 'name', { min: 1, max: MAX_GROUP_NAME_BYTES })
      stringField(c['description'], 'description', { max: MAX_GROUP_DESCRIPTION_BYTES })
      if (typeof c['public'] !== 'boolean') throw new SemanticValidationError('public must be a boolean')
      const founder = pubkeyField(c['founder'], 'founder')
      if (founder !== event.author) throw new SemanticValidationError('founder must equal author')
      const admins = c['admins']
      if (!Array.isArray(admins) || admins.length < 1 || admins.length > MAX_ADMINS) {
        throw new SemanticValidationError('admins must be a non-empty bounded array')
      }
      for (const admin of admins) pubkeyField(admin, 'admin')
      if (!admins.includes(founder)) throw new SemanticValidationError('admins must include founder')
      const relays = c['relays']
      if (!Array.isArray(relays) || relays.length < 1 || relays.length > MAX_RELAYS) {
        throw new SemanticValidationError('relays must be a non-empty bounded array')
      }
      for (const relay of relays) relayUrl(relay)
      const app = stringField(c['app'], 'app', { min: 1, max: MAX_APP_NAME_BYTES })
      if (app === 'chat') {
        const channels = c['chat.channels']
        if (!Array.isArray(channels) || channels.length === 0) {
          throw new SemanticValidationError('chat.channels must be a non-empty array')
        }
        const ids = channels.map(validateChatChannel)
        if (!ids.includes('general')) throw new SemanticValidationError('chat.channels must include general')
        if ('chat.default_channel' in c) channelId(c['chat.default_channel'], 'chat.default_channel')
        if ('chat.system_channel' in c) channelId(c['chat.system_channel'], 'chat.system_channel')
      }
      return
    }
    case 'join':
    case 'leave':
      only(c, [])
      return
    case 'invite':
      only(c, ['invitee', 'role'])
      pubkeyField(c['invitee'], 'invitee')
      if (c['role'] !== 'member') throw new SemanticValidationError('role must be member')
      return
    case 'kick':
    case 'unban':
    case 'admin_add':
    case 'admin_remove':
      only(c, ['target'])
      pubkeyField(c['target'], 'target')
      return
    case 'ban': {
      only(c, ['target', 'until', 'reason'])
      pubkeyField(c['target'], 'target')
      if (c['until'] !== undefined && c['until'] !== null) {
        const until = integerField(c['until'], 'until')
        if (until <= 0) throw new SemanticValidationError('until must be positive')
      }
      stringField(c['reason'] ?? '', 'reason', { max: MAX_BAN_REASON_BYTES })
      return
    }
    case 'relay_update': {
      only(c, ['relays'])
      const relays = c['relays']
      if (!Array.isArray(relays) || relays.length < 1 || relays.length > MAX_RELAYS) {
        throw new SemanticValidationError('relays must be a non-empty bounded array')
      }
      for (const relay of relays) relayUrl(relay)
      return
    }
    case 'metadata_update':
      only(c, ['name', 'description'])
      if (!('name' in c) && !('description' in c)) {
        throw new SemanticValidationError('metadata_update must include a field')
      }
      if ('name' in c) stringField(c['name'], 'name', { min: 1, max: MAX_GROUP_NAME_BYTES })
      if ('description' in c) stringField(c['description'], 'description', { max: MAX_GROUP_DESCRIPTION_BYTES })
      return
    case 'chat.message':
      only(c, ['text', 'channel', 'reply_to'])
      stringField(c['text'], 'text', { min: 1, max: MAX_MESSAGE_TEXT_BYTES })
      channelId(c['channel'], 'channel')
      if (c['reply_to'] !== undefined && c['reply_to'] !== null) eventIdField(c['reply_to'], 'reply_to')
      return
    case 'chat.reaction':
      only(c, ['target', 'emoji'])
      eventIdField(c['target'], 'target')
      stringField(c['emoji'], 'emoji', { min: 1, max: MAX_REACTION_BYTES })
      return
    case 'chat.nickname_set':
      only(c, ['nickname'])
      stringField(c['nickname'], 'nickname', { min: 1, max: MAX_NICKNAME_BYTES })
      return
    case 'chat.channel_create':
      only(c, ['name', 'description', 'position'])
      stringField(c['name'], 'name', { min: 1, max: MAX_CHANNEL_NAME_BYTES })
      if ('description' in c) stringField(c['description'], 'description', { max: MAX_CHANNEL_DESCRIPTION_BYTES })
      if ('position' in c) integerField(c['position'], 'position')
      return
    case 'chat.channel_update':
      only(c, ['id', 'name', 'description', 'position'])
      channelId(c['id'], 'id')
      if (!('name' in c) && !('description' in c) && !('position' in c)) {
        throw new SemanticValidationError('channel_update must include an update')
      }
      if ('name' in c) stringField(c['name'], 'name', { min: 1, max: MAX_CHANNEL_NAME_BYTES })
      if ('description' in c) stringField(c['description'], 'description', { max: MAX_CHANNEL_DESCRIPTION_BYTES })
      if ('position' in c) integerField(c['position'], 'position')
      return
    case 'chat.channel_delete':
      only(c, ['id', 'name'])
      if (channelId(c['id'], 'id') === 'general') throw new SemanticValidationError('general channel cannot be deleted')
      if ('name' in c) stringField(c['name'], 'name', { min: 1, max: MAX_CHANNEL_NAME_BYTES })
      return
    case 'chat.settings_update':
      only(c, ['default_channel', 'system_channel'])
      if (!('default_channel' in c) && !('system_channel' in c)) {
        throw new SemanticValidationError('settings_update must include a field')
      }
      if ('default_channel' in c) channelId(c['default_channel'], 'default_channel')
      if ('system_channel' in c) channelId(c['system_channel'], 'system_channel')
      return
    default:
      throw new SemanticValidationError(`unknown event type: ${event.type}`)
  }
}
