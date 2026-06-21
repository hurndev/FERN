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

const NODE_COLORS = {
  genesis: { background: '#dfe9f6', border: '#385f8a' },
  connected: { background: '#e7f0e8', border: '#3a6b40' },
  disconnected: { background: '#fdf6e3', border: '#8b6914' },
  rejected: { background: '#f7e8e8', border: '#8b2020' },
  missing: { background: '#ecede7', border: '#99a090' },
}

function eventLabel(event: FernEvent): string {
  const type = event.type.length > 18 ? event.type.replace('chat.', 'c.') : event.type
  return `${type}\n${truncateId(event.id)}`
}

function nodeColor(event: FernEvent, status: DagNode['status']): Node['color'] {
  if (event.type === 'genesis') {
    const color = NODE_COLORS.genesis
    return { background: color.background, border: color.border, highlight: { background: color.background, border: color.border } }
  }
  if (status === 'rejected') {
    const color = NODE_COLORS.rejected
    return { background: color.background, border: color.border, highlight: { background: color.background, border: color.border } }
  }
  if (status === 'disconnected') {
    const color = NODE_COLORS.disconnected
    return { background: color.background, border: color.border, highlight: { background: color.background, border: color.border } }
  }
  const color = NODE_COLORS.connected
  return { background: color.background, border: color.border, highlight: { background: color.background, border: color.border } }
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

export function DagViewer({ groupName, groupPubkey, events, onClose }: Props) {
  const graphRef = useRef<HTMLDivElement>(null)
  const networkRef = useRef<Network | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const dagNodes = useMemo(() => buildDagNodes(events), [events])
  const nodeById = useMemo(() => new Map(dagNodes.map((node) => [node.id, node])), [dagNodes])
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
    const nodes = new DataSet<Node>(
      dagNodes.map((node) => ({
        id: node.id,
        label: node.label,
        shape: node.event?.type === 'genesis' ? 'diamond' : node.kind === 'missing' ? 'box' : 'dot',
        size: node.event?.type === 'genesis' ? 22 : node.kind === 'missing' ? undefined : 17,
        color: node.kind === 'missing'
          ? { background: NODE_COLORS.missing.background, border: NODE_COLORS.missing.border, highlight: { background: NODE_COLORS.missing.background, border: NODE_COLORS.missing.border } }
          : node.event
            ? nodeColor(node.event, node.status)
            : undefined,
        font: {
          face: 'JetBrains Mono',
          size: 11,
          color: '#1a1c17',
          multi: true,
        },
        borderWidth: node.status === 'connected' ? 1 : 2,
      })),
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
  }, [dagNodes, events])

  const selected = selectedId ? nodeById.get(selectedId) ?? null : null
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
          <span className={styles.dagStat}>events {stats.total}</span>
          <span className={styles.dagStat}>connected {stats.connected}</span>
          <span className={styles.dagStat}>disconnected {stats.disconnected}</span>
          <span className={styles.dagStat}>rejected {stats.rejected}</span>
          <span className={styles.dagStat}>missing parents {stats.missing}</span>
        </div>
      </div>

      <div className={styles.dagBody}>
        <div className={styles.dagGraphWrap}>
          <div className={styles.dagGraph} ref={graphRef} />
          <div className={styles.dagLegendPanel}>
            <span className={styles.dagLegendItem}>
              <span className={`${styles.dagLegendSwatch} ${styles.dagLegendGenesis}`} />
              genesis
            </span>
            <span className={styles.dagLegendItem}>
              <span className={`${styles.dagLegendSwatch} ${styles.dagLegendConnected}`} />
              connected
            </span>
            <span className={styles.dagLegendItem}>
              <span className={`${styles.dagLegendSwatch} ${styles.dagLegendDisconnected}`} />
              disconnected
            </span>
            <span className={styles.dagLegendItem}>
              <span className={`${styles.dagLegendSwatch} ${styles.dagLegendRejected}`} />
              rejected
            </span>
            <span className={styles.dagLegendItem}>
              <span className={`${styles.dagLegendSwatch} ${styles.dagLegendMissing}`} />
              missing
            </span>
          </div>
        </div>
        <aside className={styles.dagInspector}>
          {selected?.event ? (
            <>
              <div className={styles.dagInspectorTitle}>{selected.event.type}</div>
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
          ) : (
            <div className={styles.dagEmptyInspector}>Select a node to inspect the stored event.</div>
          )}
        </aside>
      </div>
    </div>
  )
}
