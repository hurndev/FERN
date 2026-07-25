import { openDB, type DBSchema, type IDBPDatabase } from 'idb'
import type { Commit } from './bft'
import type { FernEvent } from './events'
import type { IngressReceipt } from './validator'

interface BrackenDB extends DBSchema {
  events: {
    key: string
    value: FernEvent
    indexes: { 'by-group': string; 'by-ts': number }
  }
  commits: {
    key: string
    value: { key: string; group: string; height: number; commit: Commit }
    indexes: { 'by-group': string }
  }
  ingressReceipts: {
    key: string
    value: { key: string; event_id: string; validator: string; receipt: IngressReceipt }
    indexes: { 'by-event': string }
  }
  identity: {
    key: string
    value: { pubkey: string; seed: string; secretKey: string }
  }
  validatorPins: {
    key: string
    value: { url: string; pubkey: string }
  }
  meta: {
    key: string
    value: { key: string; value: unknown }
  }
}

let db: IDBPDatabase<BrackenDB> | null = null

export async function getDB(): Promise<IDBPDatabase<BrackenDB>> {
  if (db) return db
  db = await openDB<BrackenDB>('bracken', 4, {
    upgrade(database, oldVersion, _newVersion, transaction) {
      // Legacy object stores are not part of the typed schema, so name checks
      // and deletions go through the untyped IDBDatabase interface.
      const rawDatabase = database as unknown as IDBDatabase
      if (oldVersion < 1) {
        const events = database.createObjectStore('events', { keyPath: 'id' })
        events.createIndex('by-group', 'group')
        events.createIndex('by-ts', 'ts')
        database.createObjectStore('identity', { keyPath: 'pubkey' })
        database.createObjectStore('meta', { keyPath: 'key' })
      }
      if (oldVersion < 3) {
        // Legacy DAG events and receipts are not valid fern-bft-1 objects.
        if (rawDatabase.objectStoreNames.contains('events')) transaction.objectStore('events').clear()
        if (rawDatabase.objectStoreNames.contains('event_receipts'))
          rawDatabase.deleteObjectStore('event_receipts')
        if (!database.objectStoreNames.contains('commits')) {
          const commits = database.createObjectStore('commits', { keyPath: 'key' })
          commits.createIndex('by-group', 'group')
        }
        if (!database.objectStoreNames.contains('ingressReceipts')) {
          const receipts = database.createObjectStore('ingressReceipts', { keyPath: 'key' })
          receipts.createIndex('by-event', 'event_id')
        }
      }
      if (oldVersion < 4) {
        if (rawDatabase.objectStoreNames.contains('relayPins'))
          rawDatabase.deleteObjectStore('relayPins')
        if (rawDatabase.objectStoreNames.contains('trustLedger'))
          rawDatabase.deleteObjectStore('trustLedger')
        if (!database.objectStoreNames.contains('validatorPins'))
          database.createObjectStore('validatorPins', { keyPath: 'url' })
      }
    },
  })
  return db
}

export async function putEvent(event: FernEvent): Promise<void> {
  const database = await getDB()
  await database.put('events', event)
}

export async function putPendingEvent(event: FernEvent): Promise<void> {
  const database = await getDB()
  const transaction = database.transaction('events', 'readwrite')
  const existing = await transaction.objectStore('events').get(event.id)
  if (existing?.bft?.status !== 'finalized') {
    await transaction.objectStore('events').put({ ...event, bft: { status: 'pending' } })
  }
  await transaction.done
}

export async function getEvent(id: string): Promise<FernEvent | undefined> {
  return (await getDB()).get('events', id)
}

export async function getGroupEvents(group: string): Promise<FernEvent[]> {
  const events = await (await getDB()).getAllFromIndex('events', 'by-group', group)
  return events.sort((a, b) => {
    if (a.type === 'genesis') return -1
    if (b.type === 'genesis') return 1
    if (a.bft?.status === 'finalized' && b.bft?.status !== 'finalized') return -1
    if (a.bft?.status !== 'finalized' && b.bft?.status === 'finalized') return 1
    return (a.bft?.height ?? Number.MAX_SAFE_INTEGER) - (b.bft?.height ?? Number.MAX_SAFE_INTEGER)
      || (a.bft?.position ?? 0) - (b.bft?.position ?? 0)
      || a.ts - b.ts
  })
}

export async function putCommit(commit: Commit): Promise<void> {
  const database = await getDB()
  const transaction = database.transaction(['commits', 'events'], 'readwrite')
  const key = `${commit.group}:${String(commit.height).padStart(16, '0')}`
  await transaction.objectStore('commits').put({ key, group: commit.group, height: commit.height, commit })
  const allEvents = [
    ...commit.block.candidate.events,
    ...(commit.block.candidate.governance ? [commit.block.candidate.governance] : []),
  ]
  const existing = await transaction.objectStore('events').index('by-group').getAll(commit.group)
  for (let position = 0; position < allEvents.length; position++) {
    for (const pending of existing) {
      if (pending.id !== allEvents[position].id && pending.bft?.status === 'pending' &&
        pending.author === allEvents[position].author && pending.seq === allEvents[position].seq)
        await transaction.objectStore('events').delete(pending.id)
    }
    await transaction.objectStore('events').put({
      ...allEvents[position],
      bft: {
        status: 'finalized',
        height: commit.height,
        position,
        certifiedTimeMs: commit.block.certified_times_ms[position],
      },
    })
  }
  await transaction.done
}

export async function getCommits(group: string): Promise<Commit[]> {
  const rows = await (await getDB()).getAllFromIndex('commits', 'by-group', group)
  return rows.sort((a, b) => a.height - b.height).map((row) => row.commit)
}

export async function putIngressReceipt(receipt: IngressReceipt): Promise<void> {
  const database = await getDB()
  await database.put('ingressReceipts', {
    key: `${receipt.event_id}:${receipt.validator}`,
    event_id: receipt.event_id,
    validator: receipt.validator,
    receipt,
  })
}

export async function getIngressReceipts(eventId: string): Promise<IngressReceipt[]> {
  const rows = await (await getDB()).getAllFromIndex('ingressReceipts', 'by-event', eventId)
  return rows.map((row) => row.receipt)
}

export async function getEventIds(): Promise<Set<string>> {
  return new Set(await (await getDB()).getAllKeys('events') as string[])
}

export async function saveIdentity(identity: {
  pubkey: string; seed: string; secretKey: string
}): Promise<void> {
  const database = await getDB()
  await database.clear('identity')
  await database.put('identity', identity)
}

export async function getIdentity(): Promise<{
  pubkey: string; seed: string; secretKey: string
} | undefined> {
  return (await (await getDB()).getAll('identity'))[0]
}

export async function clearLocalData(): Promise<void> {
  const database = await getDB()
  await Promise.all([
    database.clear('events'), database.clear('commits'), database.clear('ingressReceipts'),
    database.clear('identity'), database.clear('validatorPins'),
    database.clear('meta'),
  ])
}

export async function putValidatorPin(url: string, pubkey: string): Promise<void> {
  await (await getDB()).put('validatorPins', { url, pubkey })
}

export async function getValidatorPin(url: string): Promise<string | undefined> {
  return (await (await getDB()).get('validatorPins', url))?.pubkey
}

export async function setMeta(key: string, value: unknown): Promise<void> {
  await (await getDB()).put('meta', { key, value })
}

export async function getMeta<T>(key: string): Promise<T | undefined> {
  return (await (await getDB()).get('meta', key))?.value as T | undefined
}

export async function advanceHead(group: string, head: { height: number }): Promise<void> {
  const database = await getDB()
  const transaction = database.transaction('meta', 'readwrite')
  const store = transaction.objectStore('meta')
  const key = `bft-head:${group}`
  const current = (await store.get(key))?.value as { height: number } | undefined
  if (!current || head.height >= current.height) await store.put({ key, value: head })
  await transaction.done
}
