import type { FernEvent } from './events'

export function computeConnectedEventIds(events: FernEvent[]): Set<string> {
  const byId = new Map(events.map((event) => [event.id, event]))
  const connected = new Set<string>()
  const pending = new Set(byId.keys())

  let changed = true
  while (changed) {
    changed = false
    for (const id of [...pending]) {
      const event = byId.get(id)
      if (!event) continue

      const isGenesis = event.type === 'genesis' && event.parents.length === 0
      const parentsConnected =
        event.parents.length > 0 && event.parents.every((parentId) => connected.has(parentId))

      if (isGenesis || parentsConnected) {
        connected.add(id)
        pending.delete(id)
        changed = true
      }
    }
  }

  return connected
}

export function filterConnectedEvents(events: FernEvent[]): FernEvent[] {
  const connected = computeConnectedEventIds(events)
  return events.filter((event) => connected.has(event.id))
}

export function computeConnectedTips(
  events: FernEvent[],
  excludedIds: Set<string> = new Set(),
): string[] {
  const connected = computeConnectedEventIds(events)
  const eligibleIds = new Set(
    events
      .filter((event) => connected.has(event.id) && !excludedIds.has(event.id))
      .map((event) => event.id),
  )
  const referenced = new Set<string>()

  for (const event of events) {
    if (!eligibleIds.has(event.id)) continue
    for (const parentId of event.parents) {
      if (eligibleIds.has(parentId)) referenced.add(parentId)
    }
  }

  return [...eligibleIds].filter((id) => !referenced.has(id)).sort()
}
