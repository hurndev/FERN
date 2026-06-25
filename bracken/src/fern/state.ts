import type { FernEvent } from './events'
import { log } from './logger'
import { validateEventSemantics } from './semantic'

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
  members: Set<string>
  joined: Set<string>
  banned: Map<string, BanEntry>
  admins: Set<string>
  relays: string[]
  metadata: { name: string; description: string }
  public: boolean
  app: string
  channels: Map<string, Channel>
  chatSettings: { default_channel: string; system_channel: string }
}

const PROTOCOL_TYPES = new Set([
  'genesis', 'join', 'leave', 'invite', 'kick', 'ban', 'unban',
  'admin_add', 'admin_remove', 'relay_update', 'metadata_update',
])

function channelFromConfig(raw: unknown, position: number): Channel {
  if (typeof raw === 'object' && raw !== null && !Array.isArray(raw)) {
    const record = raw as Record<string, unknown>
    const id = String(record['id'] ?? '').trim()
    const name = String(record['name'] ?? '').trim()
    const rawPosition = record['position']
    return {
      id,
      name: name || id,
      description: String(record['description'] ?? ''),
      position: typeof rawPosition === 'number' ? rawPosition : position,
    }
  }
  throw new Error('channel config must be a dict with id and name fields')
}

function initialiseFromGenesis(genesis: FernEvent): GroupState {
  const c = genesis.content
  const founder = c['founder'] as string
  const app = c['app'] as string
  const channels = new Map<string, Channel>()
  if (app === 'chat') {
    const raw = c['chat.channels'] as unknown[]
    raw.forEach((entry, idx) => {
      const channel = channelFromConfig(entry, idx)
      if (channel.id) channels.set(channel.id, channel)
    })
    if (channels.size === 0) {
      throw new Error('chat.channels must contain at least one channel')
    }
  }
  const firstChannelId = channels.size > 0 ? channels.keys().next().value! : ''
  return {
    members: new Set([founder]),
    joined: new Set([founder]),
    banned: new Map(),
    admins: new Set(c['admins'] as string[]),
    relays: c['relays'] as string[],
    metadata: {
      name: (c['name'] as string) ?? '',
      description: (c['description'] as string) ?? '',
    },
    public: c['public'] as boolean,
    app,
    channels,
    chatSettings: {
      default_channel: String(c['chat.default_channel'] ?? firstChannelId),
      system_channel: String(c['chat.system_channel'] ?? firstChannelId),
    },
  }
}

function isBannedAt(state: GroupState, pubkey: string, ts: number): boolean {
  const entry = state.banned.get(pubkey)
  if (!entry) return false
  if (entry.until === null) return true
  return entry.until > ts
}

function isAuthorised(state: GroupState, event: FernEvent): boolean {
  if (event.type === 'genesis') return true
  if (event.type === 'join' || event.type === 'leave') return true
  const adminTypes = new Set([
    'invite', 'kick', 'ban', 'unban', 'admin_add', 'admin_remove',
    'relay_update', 'metadata_update',
    'chat.channel_create', 'chat.channel_update', 'chat.channel_delete', 'chat.settings_update',
  ])
  if (adminTypes.has(event.type)) return state.admins.has(event.author)
  if (event.type.startsWith('chat.')) {
    return state.joined.has(event.author) && !isBannedAt(state, event.author, event.ts)
  }
  return true
}

function applyEvent(state: GroupState, event: FernEvent): GroupState {
  const c = event.content
  const t = event.type
  const members = new Set(state.members)
  const joined = new Set(state.joined)
  const banned = new Map(state.banned)
  const admins = new Set(state.admins)
  const relays = [...state.relays]
  const metadata = { ...state.metadata }
  const channels = new Map(state.channels)
  const chatSettings = { ...state.chatSettings }

  switch (t) {
    case 'invite':
      members.add(c['invitee'] as string)
      break
    case 'join':
      if (state.public || members.has(event.author)) {
        if (!isBannedAt(state, event.author, event.ts)) {
          joined.add(event.author)
        }
      }
      break
    case 'leave':
      joined.delete(event.author)
      break
    case 'kick':
      joined.delete(c['target'] as string)
      admins.delete(c['target'] as string)
      break
    case 'ban':
      banned.set(c['target'] as string, {
        until: (c['until'] as number | null) ?? null,
        reason: (c['reason'] as string) ?? '',
      })
      joined.delete(c['target'] as string)
      admins.delete(c['target'] as string)
      break
    case 'unban':
      banned.delete(c['target'] as string)
      break
    case 'admin_add':
      admins.add(c['target'] as string)
      break
    case 'admin_remove':
      admins.delete(c['target'] as string)
      break
    case 'relay_update':
      relays.splice(0, relays.length, ...(c['relays'] as string[]))
      break
    case 'metadata_update':
      if ('name' in c) metadata.name = c['name'] as string
      if ('description' in c) metadata.description = c['description'] as string
      break
    case 'chat.channel_create':
      {
        const id = c['id'] as string
        const name = c['name'] as string
        const duplicateName = [...channels.values()].some((channel) => channel.name === name)
        if (id && name && !duplicateName) {
          const position = typeof c['position'] === 'number' ? c['position'] as number : channels.size
          channels.set(id, {
            id,
            name,
            description: (c['description'] as string) ?? '',
            position,
          })
        }
      }
      break
    case 'chat.channel_update':
      {
        const id = c['id'] as string
        const existing = channels.get(id)
        if (existing) {
          channels.set(id, {
            id,
            name: (c['name'] as string) ?? existing.name,
            description: (c['description'] as string) ?? existing.description,
            position: typeof c['position'] === 'number' ? c['position'] as number : existing.position,
          })
        }
      }
      break
    case 'chat.channel_delete':
      {
        const id = c['id'] as string
        const defaultId = chatSettings.default_channel ?? ''
        if (id && id !== defaultId) {
          channels.delete(id)
          const firstRemaining = channels.size > 0 ? channels.keys().next().value! : ''
          if (chatSettings.default_channel === id) chatSettings.default_channel = firstRemaining
          if (chatSettings.system_channel === id) chatSettings.system_channel = firstRemaining
        }
      }
      break
    case 'chat.settings_update':
      {
        const defaultChannel = c['default_channel'] as string | undefined
        const systemChannel = c['system_channel'] as string | undefined
        if (defaultChannel && channels.has(defaultChannel)) chatSettings.default_channel = defaultChannel
        if (systemChannel && channels.has(systemChannel)) chatSettings.system_channel = systemChannel
      }
      break
  }

  return {
    members, joined, banned, admins, relays, metadata, public: state.public,
    app: state.app, channels, chatSettings,
  }
}

function validateStateDependentSemantics(state: GroupState, event: FernEvent): void {
  if (event.type === 'chat.message' && !state.channels.has(event.content['channel'] as string)) {
    throw new Error('message channel does not exist')
  }
  if (event.type === 'chat.channel_create') {
    const id = event.content['id'] as string
    if (state.channels.has(id)) {
      throw new Error('channel id already exists')
    }
    if ([...state.channels.values()].some((channel) => channel.name === event.content['name'])) {
      throw new Error('channel name already exists')
    }
  }
  if (event.type === 'chat.channel_delete') {
    const id = event.content['id'] as string
    const defaultId = state.chatSettings.default_channel ?? ''
    if (id === defaultId) {
      throw new Error('cannot delete the default channel')
    }
  }
}

export function deriveGroupState(events: FernEvent[]): {
  state: GroupState | null
  rejected: FernEvent[]
  acceptedIds: Set<string>
  genesis: FernEvent | null
} {
  let rejected: FernEvent[] = []
  const genesisEvents = events.filter((e) => e.type === 'genesis' && e.parents.length === 0)
  let genesis: FernEvent | null = null
  for (const event of genesisEvents) {
    try {
      validateEventSemantics(event)
      genesis = event
      break
    } catch (err) {
      log.stateEventRejected(event.type, event.id, `genesis semantic: ${err}`)
      rejected.push(event)
    }
  }
  if (!genesis) {
    return { state: null, rejected, acceptedIds: new Set(), genesis: null }
  }
  let state = initialiseFromGenesis(genesis)
  const acceptedIds = new Set<string>([genesis.id])

  const nonGenesis = events
    .filter((e) => e.type !== 'genesis')
    .sort((a, b) => {
      if (a.ts !== b.ts) return a.ts - b.ts
      return a.id < b.id ? -1 : a.id > b.id ? 1 : 0
    })

  const eventTs = new Map<string, number>()
  for (const e of events) eventTs.set(e.id, e.ts)

  for (const event of nonGenesis) {
    if (!event.parents.every((parent) => acceptedIds.has(parent))) {
      log.stateEventRejected(event.type, event.id, 'missing parent(s)')
      rejected.push(event)
      continue
    }
    const maxParentTs = Math.max(...event.parents.map((p) => eventTs.get(p) ?? 0))
    if (event.ts < maxParentTs) {
      log.stateEventRejected(event.type, event.id, `ts ${event.ts} < max parent ts ${maxParentTs}`)
      rejected.push(event)
      continue
    }
    try {
      validateEventSemantics(event)
      validateStateDependentSemantics(state, event)
    } catch (err) {
      log.stateEventRejected(event.type, event.id, `semantic: ${err}`)
      rejected.push(event)
      continue
    }
    if (!isAuthorised(state, event)) {
      log.stateEventRejected(event.type, event.id, 'unauthorized author')
      rejected.push(event)
      continue
    }
    state = applyEvent(state, event)
    acceptedIds.add(event.id)
  }

  let changed = true
  while (changed) {
    changed = false
    const stillRejected: FernEvent[] = []
    for (const event of rejected) {
      if (!event.parents.every((parent) => acceptedIds.has(parent))) {
        stillRejected.push(event)
        continue
      }
      const maxParentTs = Math.max(...event.parents.map((p) => eventTs.get(p) ?? 0))
      if (event.ts < maxParentTs) {
        stillRejected.push(event)
        continue
      }
      try {
        validateEventSemantics(event)
        validateStateDependentSemantics(state, event)
      } catch {
        stillRejected.push(event)
        continue
      }
      if (!isAuthorised(state, event)) {
        stillRejected.push(event)
        continue
      }
      state = applyEvent(state, event)
      acceptedIds.add(event.id)
      changed = true
    }
    rejected = stillRejected
  }

  return { state, rejected, acceptedIds, genesis }
}

export { isBannedAt, isAuthorised, PROTOCOL_TYPES }
