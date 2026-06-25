import { openDB, type DBSchema, type IDBPDatabase } from 'idb'
import type { FernEvent } from './events'
import { deriveGroupState } from './state'

interface BrackenDB extends DBSchema {
  events: {
    key: string
    value: FernEvent
    indexes: { 'by-group': string; 'by-ts': number }
  }
  event_receipts: {
    key: string
    value: {
      event_id: string
      group: string
      relay: string
      ts: number
      sig: string
    }
    indexes: { 'by-event': string }
  }
  identity: {
    key: string
    value: {
      pubkey: string
      seed: string
      secretKey: string
    }
  }
  relayPins: {
    key: string
    value: { url: string; pubkey: string }
  }
  trustLedger: {
    key: string
    value: {
      relay_pubkey: string
      last_group_status: unknown
      observed_faults: { ts: number; kind: string; event_id?: string; evidence: string }[]
    }
  }
  meta: {
    key: string
    value: { key: string; value: unknown }
  }
}

let db: IDBPDatabase<BrackenDB> | null = null

export async function getDB(): Promise<IDBPDatabase<BrackenDB>> {
  if (db) return db
  db = await openDB<BrackenDB>('bracken', 2, {
    upgrade(db, oldVersion) {
      if (oldVersion < 1) {
        const events = db.createObjectStore('events', { keyPath: 'id' })
        events.createIndex('by-group', 'group')
        events.createIndex('by-ts', 'ts')
        db.createObjectStore('identity', { keyPath: 'pubkey' })
        db.createObjectStore('relayPins', { keyPath: 'url' })
        db.createObjectStore('trustLedger', { keyPath: 'relay_pubkey' })
        db.createObjectStore('meta', { keyPath: 'key' })
      }
      if (oldVersion < 2) {
        if (!db.objectStoreNames.contains('event_receipts')) {
          const event_receipts = db.createObjectStore('event_receipts', { keyPath: 'event_id' })
          event_receipts.createIndex('by-event', 'event_id')
        }
      }
    },
  })
  return db
}

export async function putEvent(event: FernEvent): Promise<void> {
  const d = await getDB()
  await d.put('events', event)
}

export async function getEvent(id: string): Promise<FernEvent | undefined> {
  const d = await getDB()
  return d.get('events', id)
}

export async function hasEvent(id: string): Promise<boolean> {
  const d = await getDB()
  const e = await d.get('events', id)
  return e !== undefined
}

export async function getAllEvents(): Promise<FernEvent[]> {
  const d = await getDB()
  return d.getAllFromIndex('events', 'by-ts')
}

export async function getGroupEvents(group: string): Promise<FernEvent[]> {
  const d = await getDB()
  return d.getAllFromIndex('events', 'by-group', group)
}

export async function getEventIds(): Promise<Set<string>> {
  const d = await getDB()
  const all = await d.getAllKeys('events')
  return new Set(all as string[])
}

export async function getGroupEventIds(group: string): Promise<Set<string>> {
  const d = await getDB()
  const keys = await d.getAllKeysFromIndex('events', 'by-group', group)
  return new Set(keys as string[])
}

export async function getTips(group: string, excludedIds: Set<string> = new Set()): Promise<string[]> {
  const events = await getGroupEvents(group)
  const { acceptedIds } = deriveGroupState(events)
  const eligibleIds = new Set([...acceptedIds].filter((id) => !excludedIds.has(id)))
  const referenced = new Set<string>()
  for (const event of events) {
    if (!eligibleIds.has(event.id)) continue
    for (const parentId of event.parents) {
      if (eligibleIds.has(parentId)) referenced.add(parentId)
    }
  }
  return [...eligibleIds].filter((id) => !referenced.has(id)).sort()
}

export async function putEventReceipt(event_receipt: {
  event_id: string
  group: string
  relay: string
  ts: number
  sig: string
}): Promise<void> {
  const d = await getDB()
  await d.put('event_receipts', event_receipt)
}

export async function getEventReceiptsForEvent(eventId: string): Promise<
  { event_id: string; group: string; relay: string; ts: number; sig: string }[]
> {
  const d = await getDB()
  return d.getAllFromIndex('event_receipts', 'by-event', eventId)
}

export async function saveIdentity(identity: {
  pubkey: string
  seed: string
  secretKey: string
}): Promise<void> {
  const d = await getDB()
  await d.clear('identity')
  await d.put('identity', identity)
}

export async function getIdentity(): Promise<{
  pubkey: string
  seed: string
  secretKey: string
} | undefined> {
  const d = await getDB()
  const all = await d.getAll('identity')
  return all[0]
}

export async function clearLocalData(): Promise<void> {
  const d = await getDB()
  await Promise.all([
    d.clear('events'),
    d.clear('event_receipts'),
    d.clear('identity'),
    d.clear('relayPins'),
    d.clear('trustLedger'),
    d.clear('meta'),
  ])
}

export async function putRelayPin(url: string, pubkey: string): Promise<void> {
  const d = await getDB()
  await d.put('relayPins', { url, pubkey })
}

export async function getRelayPin(url: string): Promise<string | undefined> {
  const d = await getDB()
  const pin = await d.get('relayPins', url)
  return pin?.pubkey
}

export async function setMeta(key: string, value: unknown): Promise<void> {
  const d = await getDB()
  await d.put('meta', { key, value })
}

export async function getMeta<T>(key: string): Promise<T | undefined> {
  const d = await getDB()
  const m = await d.get('meta', key)
  return m?.value as T | undefined
}
