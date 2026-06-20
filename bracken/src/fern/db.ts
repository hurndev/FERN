import { openDB, type DBSchema, type IDBPDatabase } from 'idb'
import type { FernEvent } from './events'
import { computeConnectedTips } from './dag'

interface BrackenDB extends DBSchema {
  events: {
    key: string
    value: FernEvent
    indexes: { 'by-group': string; 'by-ts': number }
  }
  receipts: {
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
      last_attestation: unknown
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
  db = await openDB<BrackenDB>('bracken', 1, {
    upgrade(db) {
      const events = db.createObjectStore('events', { keyPath: 'id' })
      events.createIndex('by-group', 'group')
      events.createIndex('by-ts', 'ts')
      const receipts = db.createObjectStore('receipts', { keyPath: 'event_id' })
      receipts.createIndex('by-event', 'event_id')
      db.createObjectStore('identity', { keyPath: 'pubkey' })
      db.createObjectStore('relayPins', { keyPath: 'url' })
      db.createObjectStore('trustLedger', { keyPath: 'relay_pubkey' })
      db.createObjectStore('meta', { keyPath: 'key' })
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
  return computeConnectedTips(events, excludedIds)
}

export async function putReceipt(receipt: {
  event_id: string
  group: string
  relay: string
  ts: number
  sig: string
}): Promise<void> {
  const d = await getDB()
  await d.put('receipts', receipt)
}

export async function getReceiptsForEvent(eventId: string): Promise<
  { event_id: string; group: string; relay: string; ts: number; sig: string }[]
> {
  const d = await getDB()
  return d.getAllFromIndex('receipts', 'by-event', eventId)
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
    d.clear('receipts'),
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
