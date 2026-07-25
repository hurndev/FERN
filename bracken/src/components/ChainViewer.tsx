import { useMemo, useState } from 'react'
import type { FernEvent } from '../fern/events'
import { absoluteTime, truncateId } from '../fern/utils'
import styles from '../styles/components.module.css'

interface Props {
  groupName: string
  groupPubkey: string
  events: FernEvent[]
  validatorCount: number
  onClose: () => void
}

function comparePosition(a: FernEvent, b: FernEvent): number {
  if (a.type === 'genesis') return -1
  if (b.type === 'genesis') return 1
  if (a.bft?.status !== b.bft?.status) return a.bft?.status === 'finalized' ? -1 : 1
  return (a.bft?.height ?? Number.MAX_SAFE_INTEGER) - (b.bft?.height ?? Number.MAX_SAFE_INTEGER)
    || (a.bft?.position ?? Number.MAX_SAFE_INTEGER) - (b.bft?.position ?? Number.MAX_SAFE_INTEGER)
    || a.seq - b.seq
    || a.id.localeCompare(b.id)
}

export function ChainViewer({ groupName, groupPubkey, events, validatorCount, onClose }: Props) {
  const [filterType, setFilterType] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const ordered = useMemo(
    () => [...events].filter((event) => !filterType || event.type === filterType).sort(comparePosition),
    [events, filterType],
  )
  const types = useMemo(() => [...new Set(events.map((event) => event.type))].sort(), [events])
  const selected = events.find((event) => event.id === selectedId) ?? null
  const finalized = events.filter((event) => event.type === 'genesis' || event.bft?.status === 'finalized').length
  const pending = events.filter((event) => event.bft?.status === 'pending').length

  return (
    <div className={styles.dagPage}>
      <div className={styles.dagHeader}>
        <button className={styles.dagBackBtn} onClick={onClose} title="Back to chat">←</button>
        <div className={styles.dagHeading}>
          <div className={styles.dagTitle}>Finalized chain · {groupName || 'Unnamed group'}</div>
          <div className={styles.dagSubtitle}><span className="mono">{groupPubkey}</span></div>
        </div>
        <div className={styles.dagHeaderStats}>
          <select className={styles.dagFilterSelect} value={filterType} onChange={(event) => setFilterType(event.target.value)}>
            <option value="">All types</option>
            {types.map((type) => <option key={type} value={type}>{type}</option>)}
          </select>
          <span className={styles.dagStat}>finalized {finalized}</span>
          <span className={styles.dagStat}>pending {pending}</span>
        </div>
      </div>

      {validatorCount > 0 && validatorCount < 4 && (
        <div className={styles.smallSetWarning} role="alert">
          Warning: this group uses {validatorCount}-validator unanimous small-set mode.
          Every validator must participate; any unavailable validator halts consensus.
        </div>
      )}

      <div className={`${styles.dagBody} ${selected ? styles.dagBodyWithInspector : ''}`}>
        <div className={styles.dagGraphWrap} style={{ overflow: 'auto', padding: 16 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ textAlign: 'left' }}>
                <th>Position</th><th>Status</th><th>Type</th><th>Author</th><th>Event</th>
              </tr>
            </thead>
            <tbody>
              {ordered.map((event) => (
                <tr
                  key={event.id}
                  onClick={() => setSelectedId(event.id)}
                  style={{ cursor: 'pointer', borderTop: '1px solid var(--border)' }}
                >
                  <td className="mono" style={{ padding: '10px 6px' }}>
                    {event.type === 'genesis' ? '0:0' : event.bft?.status === 'finalized'
                      ? `${event.bft.height}:${event.bft.position}` : '—'}
                  </td>
                  <td>{event.type === 'genesis' ? 'finalized' : event.bft?.status ?? 'unknown'}</td>
                  <td>{event.type}</td>
                  <td className="mono">{truncateId(event.author)}</td>
                  <td className="mono">{truncateId(event.id)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {selected && (
          <aside className={styles.dagInspector}>
            <div className={styles.dagInspectorTitle}>{selected.type}</div>
            <div className={styles.dagInspectorGrid}>
              <span>Status</span><strong>{selected.type === 'genesis' ? 'finalized' : selected.bft?.status ?? 'unknown'}</strong>
              <span>Height</span><strong>{selected.bft?.height ?? 0}</strong>
              <span>Position</span><strong>{selected.bft?.position ?? 0}</strong>
              <span>Sequence</span><strong>{selected.seq}</strong>
              <span>ID</span><code>{selected.id}</code>
              <span>Author</span><code>{selected.author}</code>
              <span>Author time</span><strong>{absoluteTime(selected.ts)}</strong>
              <span>Certified time</span>
              <strong>{selected.bft?.certifiedTimeMs
                ? absoluteTime(Math.floor(selected.bft.certifiedTimeMs / 1000)) : 'genesis / pending'}</strong>
              <span>Signature</span><code>{selected.sig}</code>
            </div>
            <pre className={styles.dagJson}>{JSON.stringify(selected.content, null, 2)}</pre>
          </aside>
        )}
      </div>
    </div>
  )
}
