import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { DataSet, Network, type Edge, type Node, type Options } from 'vis-network/standalone'
import type { FernEvent } from '../fern/events'
import { computeConnectedEventIds } from '../fern/dag'
import { deriveGroupState } from '../fern/state'
import { absoluteTime, truncateId } from '../fern/utils'
import styles from '../styles/components.module.css'

interface Props {
  groupName: string
  groupPubkey: string
  events: FernEvent[]
  onClose: () => void
}

interface DagNode {
  id: string
  label: string
  kind: 'event' | 'missing'
  event?: FernEvent
  status: 'connected' | 'disconnected' | 'rejected' | 'missing'
}

type TypeGroup = 'genesis' | 'membership' | 'moderation' | 'admin' | 'infrastructure' | 'chat_message' | 'chat_social' | 'chat_channel' | 'unknown'

const TYPE_COLORS: Record<TypeGroup, string> = {
  genesis: '#e91e63',
  membership: '#4caf50',
  moderation: '#ff5722',
  admin: '#9c27b0',
  infrastructure: '#00bcd4',
  chat_message: '#1565c0',
  chat_social: '#607d8b',
  chat_channel: '#795548',
  unknown: '#ff9800',
}

const GROUP_LABELS: Record<TypeGroup, string> = {
  genesis: 'genesis',
  membership: 'membership (join/leave/invite)',
  moderation: 'moderation (kick/ban/unban)',
  admin: 'admin (add/remove)',
  infrastructure: 'infrastructure (relay/metadata)',
  chat_message: 'chat messages',
  chat_social: 'chat social (reactions/nicknames)',
  chat_channel: 'chat channels/settings',
  unknown: 'unknown',
}

function getTypeGroup(type: string): TypeGroup {
  if (type === 'genesis') return 'genesis'
  if (type === 'join' || type === 'leave' || type === 'invite') return 'membership'
  if (type === 'kick' || type === 'ban' || type === 'unban') return 'moderation'
  if (type === 'admin_add' || type === 'admin_remove') return 'admin'
  if (type === 'relay_update' || type === 'metadata_update') return 'infrastructure'
  if (type === 'chat.message') return 'chat_message'
  if (type === 'chat.reaction' || type === 'chat.nickname_set') return 'chat_social'
  if (type.startsWith('chat.')) return 'chat_channel'
  return 'unknown'
}

function getTypeColor(type: string): string {
  return TYPE_COLORS[getTypeGroup(type)] || TYPE_COLORS.unknown
}

function eventLabel(event: FernEvent): string {
  const type = event.type.length > 18 ? event.type.replace('chat.', 'c.') : event.type
  return `${type}\n${truncateId(event.id)}`
}

function getNodeShape(type: string, status: DagNode['status']): string {
  if (type === 'genesis') return 'diamond'
  if (status === 'rejected') return 'square'
  if (status === 'missing') return 'box'
  return 'dot'
}

function buildDagNodes(events: FernEvent[]): DagNode[] {
  const byId = new Map(events.map((event) => [event.id, event]))
  const connectedIds = computeConnectedEventIds(events)
  const { rejected } = deriveGroupState(events)
  const rejectedIds = new Set(rejected.map((event) => event.id))
  const nodes: DagNode[] = events.map((event) => ({
    id: event.id,
    label: eventLabel(event),
    kind: 'event',
    event,
    status: rejectedIds.has(event.id)
      ? 'rejected'
      : connectedIds.has(event.id)
        ? 'connected'
        : 'disconnected',
  }))

  const missing = new Set<string>()
  for (const event of events) {
    for (const parent of event.parents) {
      if (!byId.has(parent)) missing.add(parent)
    }
  }
  for (const id of missing) {
    nodes.push({
      id: `missing:${id}`,
      label: `missing\n${truncateId(id)}`,
      kind: 'missing',
      status: 'missing',
    })
  }
  return nodes
}

function computeNodeLevels(dagNodes: DagNode[]): Map<string, number> {
  const levels = new Map<string, number>()
  const childrenOf = new Map<string, string[]>()
  const allIds = new Set(dagNodes.map((n) => n.id))

  for (const node of dagNodes) {
    if (node.event) {
      for (const parent of node.event.parents) {
        const parentId = allIds.has(parent) ? parent : `missing:${parent}`
        if (!childrenOf.has(parentId)) childrenOf.set(parentId, [])
        childrenOf.get(parentId)!.push(node.id)
      }
    }
  }

  const genesis = dagNodes.find((n) => n.event?.type === 'genesis')
  if (!genesis) return levels

  const queue: string[] = [genesis.id]
  levels.set(genesis.id, 0)

  while (queue.length > 0) {
    const current = queue.shift()!
    const currentLevel = levels.get(current)!
    const children = childrenOf.get(current) ?? []
    for (const child of children) {
      const existing = levels.get(child)
      const newLevel = currentLevel + 1
      if (existing === undefined || newLevel > existing) {
        levels.set(child, newLevel)
        queue.push(child)
      }
    }
  }

  const maxLevel = Math.max(...levels.values(), 0)
  for (const node of dagNodes) {
    if (!levels.has(node.id)) {
      const childLevels = (childrenOf.get(node.id) ?? [])
        .map((id) => levels.get(id))
        .filter((l): l is number => l !== undefined)
      if (childLevels.length > 0) {
        levels.set(node.id, Math.min(...childLevels) - 1)
      } else {
        levels.set(node.id, maxLevel + 1)
      }
    }
  }

  return levels
}

export function DagViewer({ groupName, groupPubkey, events, onClose }: Props) {
  const graphRef = useRef<HTMLDivElement>(null)
  const networkRef = useRef<Network | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [filterType, setFilterType] = useState<string>('')

  const dagNodes = useMemo(() => buildDagNodes(events), [events])
  const nodeLevels = useMemo(() => computeNodeLevels(dagNodes), [dagNodes])
  const nodeById = useMemo(() => new Map(dagNodes.map((node) => [node.id, node])), [dagNodes])
  const uniqueTypes = useMemo(() => [...new Set(events.map((e) => e.type))].sort(), [events])
  const filteredTypeGroups = useMemo(() => {
    if (!filterType) return null
    return new Set(events.filter((e) => e.type === filterType).map((e) => e.id))
  }, [events, filterType])
  const stats = useMemo(() => {
    const connected = dagNodes.filter((node) => node.status === 'connected').length
    const disconnected = dagNodes.filter((node) => node.status === 'disconnected').length
    const rejected = dagNodes.filter((node) => node.status === 'rejected').length
    const missing = dagNodes.filter((node) => node.status === 'missing').length
    return { connected, disconnected, rejected, missing, total: events.length }
  }, [dagNodes, events.length])

  useEffect(() => {
    if (!graphRef.current) return
    const knownIds = new Set(events.map((event) => event.id))
    const isFiltered = filterType !== ''
    const nodes = new DataSet<Node>(
      dagNodes.map((node) => {
        const type = node.event?.type ?? ''
        const color = node.kind === 'missing' ? '#99a090' : getTypeColor(type)
        const shape = node.kind === 'missing' ? 'box' : getNodeShape(type, node.status)
        const isMatch = !isFiltered || !filteredTypeGroups || filteredTypeGroups.has(node.id)
        const opacity = isFiltered && !isMatch ? 0.15 : 1
        const borderWidth = (isFiltered && isMatch) ? 3 : (type === 'genesis' ? 2 : 1)
        const borderColor = (isFiltered && isMatch) ? '#ffc107' : color
        return {
          id: node.id,
          label: node.label,
          level: nodeLevels.get(node.id) ?? 0,
          shape,
          size: type === 'genesis' ? 22 : node.kind === 'missing' ? undefined : 17,
          color: {
            background: color,
            border: borderColor,
            highlight: { background: color, border: '#ffc107' },
            opacity,
          },
          font: {
            face: 'JetBrains Mono',
            size: 11,
            color: isFiltered && !isMatch ? '#ccc' : '#1a1c17',
            multi: true,
          },
          borderWidth,
        }
      }),
    )
    const edges = new DataSet<Edge>(
      events.flatMap((event) =>
        event.parents.map((parent) => ({
          id: `${parent}->${event.id}`,
          from: knownIds.has(parent) ? parent : `missing:${parent}`,
          to: event.id,
          arrows: 'to',
          color: { color: knownIds.has(parent) ? '#99a090' : '#8b6914' },
          width: knownIds.has(parent) ? 1 : 2,
        })),
      ),
    )

    const options: Options = {
      autoResize: true,
      layout: {
        hierarchical: {
          enabled: true,
          direction: 'LR',
          sortMethod: 'directed',
          levelSeparation: 170,
          nodeSpacing: 120,
        },
      },
      physics: false,
      interaction: {
        hover: true,
        multiselect: false,
        navigationButtons: false,
        keyboard: true,
      },
      nodes: {
        shadow: false,
      },
      edges: {
        smooth: { enabled: true, type: 'cubicBezier', forceDirection: 'horizontal', roundness: 0.35 },
      },
    }

    networkRef.current?.destroy()
    const network = new Network(graphRef.current, { nodes, edges }, options)
    networkRef.current = network
    network.on('selectNode', (params) => {
      const nodeId = String(params.nodes[0] ?? '')
      setSelectedId(nodeId || null)
    })
    network.on('deselectNode', () => setSelectedId(null))
    network.once('afterDrawing', () => {
      network.fit({ animation: false })
    })
    return () => {
      network.destroy()
      if (networkRef.current === network) networkRef.current = null
    }
  }, [dagNodes, events, filterType, filteredTypeGroups, nodeLevels])

  const selected = selectedId ? nodeById.get(selectedId) ?? null : null
  const hasSelection = selected !== null && (selected.event !== undefined || selected.status === 'missing')
  const focusNode = useCallback((id: string) => {
    const nodeId = nodeById.has(id) ? id : `missing:${id}`
    if (!nodeById.has(nodeId)) return
    setSelectedId(nodeId)
    networkRef.current?.selectNodes([nodeId])
    networkRef.current?.focus(nodeId, {
      scale: 1,
      animation: { duration: 250, easingFunction: 'easeInOutQuad' },
    })
  }, [nodeById])

  return (
    <div className={styles.dagPage}>
      <div className={styles.dagHeader}>
        <button className={styles.dagBackBtn} onClick={onClose} title="Back to chat">
          ←
        </button>
        <div className={styles.dagHeading}>
          <div className={styles.dagTitle}>DAG Viewer · {groupName || 'Unnamed group'}</div>
          <div className={styles.dagSubtitle}>
            <span className="mono">{groupPubkey}</span>
          </div>
        </div>
        <div className={styles.dagHeaderStats}>
          <select
            className={styles.dagFilterSelect}
            value={filterType}
            onChange={(e) => setFilterType(e.target.value)}
          >
            <option value="">All types</option>
            {uniqueTypes.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
          <span className={styles.dagStat}>events {stats.total}</span>
          <span className={styles.dagStat}>connected {stats.connected}</span>
          <span className={styles.dagStat}>disconnected {stats.disconnected}</span>
          <span className={styles.dagStat}>rejected {stats.rejected}</span>
          <span className={styles.dagStat}>missing parents {stats.missing}</span>
        </div>
      </div>

      <div className={`${styles.dagBody} ${hasSelection ? styles.dagBodyWithInspector : ''}`}>
        <div className={styles.dagGraphWrap}>
          <div className={styles.dagGraph} ref={graphRef} />
          <div className={styles.dagLegendPanel}>
            <span className={styles.dagLegendItem}>
              <svg width="12" height="12" viewBox="0 0 12 12"><polygon points="6,0 12,12 0,12" fill={TYPE_COLORS.genesis} /></svg>
              <span>genesis</span>
            </span>
            {(Object.entries(GROUP_LABELS) as [TypeGroup, string][]).filter(([g]) => g !== 'genesis').map(([group, label]) => (
              <span key={group} className={styles.dagLegendItem}>
                <span className={styles.dagLegendSwatch} style={{ background: TYPE_COLORS[group], borderColor: TYPE_COLORS[group], borderRadius: '50%' }} />
                <span>{label}</span>
              </span>
            ))}
            <span className={styles.dagLegendDivider} />
            <span className={styles.dagLegendItem}>
              <svg width="12" height="12" viewBox="0 0 12 12"><circle cx="6" cy="6" r="5" fill="#888" /></svg>
              <span>accepted (circle)</span>
            </span>
            <span className={styles.dagLegendItem}>
              <svg width="12" height="12" viewBox="0 0 12 12"><rect x="1" y="1" width="10" height="10" fill="#888" /></svg>
              <span>rejected (square)</span>
            </span>
            <span className={styles.dagLegendItem}>
              <svg width="12" height="12" viewBox="0 0 12 12"><polygon points="6,0 12,12 0,12" fill="#888" /></svg>
              <span>genesis (triangle)</span>
            </span>
          </div>
        </div>
        {hasSelection && (
          <aside className={styles.dagInspector}>
            {selected?.event ? (
              <>
                <div className={styles.dagInspectorTitle}>
                  <span className={styles.dagTypeBadge} style={{ background: getTypeColor(selected.event.type), color: '#fff' }}>
                    {selected.event.type}
                  </span>
                </div>
                <div className={styles.dagInspectorGrid}>
                  <span>Type</span>
                  <code>{selected.event.type}</code>
                  <span>Status</span>
                  <strong>{selected.status}</strong>
                  <span>ID</span>
                  <code>{selected.event.id}</code>
                  <span>Group</span>
                  <code>{selected.event.group}</code>
                  <span>Author</span>
                  <code>{selected.event.author}</code>
                  <span>Timestamp</span>
                  <strong>{absoluteTime(selected.event.ts)}</strong>
                  <span>Parents</span>
                  {selected.event.parents.length > 0 ? (
                    <div className={styles.dagParentList}>
                      {selected.event.parents.map((parent) => (
                        <button
                          key={parent}
                          className={styles.dagParentBtn}
                          onClick={() => focusNode(parent)}
                        >
                          {truncateId(parent)}
                        </button>
                      ))}
                    </div>
                  ) : (
                    <strong>none</strong>
                  )}
                  <span>Tags</span>
                  <code>{selected.event.tags.length > 0 ? JSON.stringify(selected.event.tags) : '[]'}</code>
                  <span>Signature</span>
                  <code>{selected.event.sig}</code>
                </div>
                <pre className={styles.dagJson}>{JSON.stringify(selected.event.content, null, 2)}</pre>
              </>
            ) : selected?.status === 'missing' ? (
              <>
                <div className={styles.dagInspectorTitle}>Missing parent</div>
                <div className={styles.dagInspectorGrid}>
                  <span>ID</span>
                  <code>{selected.id.replace(/^missing:/, '')}</code>
                </div>
              </>
            ) : null}
          </aside>
        )}
      </div>
    </div>
  )
}
