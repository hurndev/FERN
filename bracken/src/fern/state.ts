import type { FernEvent } from './events'
import { filterConnectedEvents } from './dag'

export interface BanEntry {
  until: number | null
  reason: string
}

export interface GroupState {
  members: Set<string>
  joined: Set<string>
  banned: Map<string, BanEntry>
  mods: Set<string>
  relays: string[]
  metadata: { name: string; description: string }
  public: boolean
}

const PROTOCOL_TYPES = new Set([
  'genesis', 'join', 'leave', 'invite', 'kick', 'ban', 'unban',
  'mod_add', 'mod_remove', 'relay_update', 'metadata_update',
])

function initialiseFromGenesis(genesis: FernEvent): GroupState {
  const c = genesis.content
  const founder = c['founder'] as string
  return {
    members: new Set([founder]),
    joined: new Set([founder]),
    banned: new Map(),
    mods: new Set(c['mods'] as string[]),
    relays: c['relays'] as string[],
    metadata: {
      name: (c['name'] as string) ?? '',
      description: (c['description'] as string) ?? '',
    },
    public: c['public'] as boolean,
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
    'invite', 'kick', 'ban', 'unban', 'mod_add', 'mod_remove',
    'relay_update', 'metadata_update',
  ])
  if (adminTypes.has(event.type)) return state.mods.has(event.author)
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
  const mods = new Set(state.mods)
  const relays = [...state.relays]
  const metadata = { ...state.metadata }

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
      mods.delete(c['target'] as string)
      break
    case 'ban':
      banned.set(c['target'] as string, {
        until: (c['until'] as number | null) ?? null,
        reason: (c['reason'] as string) ?? '',
      })
      joined.delete(c['target'] as string)
      break
    case 'unban':
      banned.delete(c['target'] as string)
      break
    case 'mod_add':
      mods.add(c['target'] as string)
      break
    case 'mod_remove':
      mods.delete(c['target'] as string)
      break
    case 'relay_update':
      relays.splice(0, relays.length, ...(c['relays'] as string[]))
      break
    case 'metadata_update':
      if ('name' in c) metadata.name = c['name'] as string
      if ('description' in c) metadata.description = c['description'] as string
      break
  }

  return {
    members, joined, banned, mods, relays, metadata, public: state.public,
  }
}

export function deriveGroupState(events: FernEvent[]): {
  state: GroupState | null
  rejected: FernEvent[]
  genesis: FernEvent | null
} {
  const connectedEvents = filterConnectedEvents(events)
  const genesisEvents = connectedEvents.filter((e) => e.type === 'genesis')
  if (genesisEvents.length === 0) {
    return { state: null, rejected: [], genesis: null }
  }
  const genesis = genesisEvents[0]
  let state = initialiseFromGenesis(genesis)

  const nonGenesis = connectedEvents
    .filter((e) => e.type !== 'genesis')
    .sort((a, b) => {
      if (a.ts !== b.ts) return a.ts - b.ts
      return a.id < b.id ? -1 : a.id > b.id ? 1 : 0
    })

  const rejected: FernEvent[] = []
  for (const event of nonGenesis) {
    if (!isAuthorised(state, event)) {
      rejected.push(event)
      continue
    }
    state = applyEvent(state, event)
  }

  return { state, rejected, genesis }
}

export { isBannedAt, isAuthorised, PROTOCOL_TYPES }
