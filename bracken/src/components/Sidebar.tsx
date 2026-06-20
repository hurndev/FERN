import { useState } from 'react'
import { FernLogo } from './FernLogo'
import { truncateId } from '../fern/utils'
import type { GroupEntry, RelayConnection } from '../hooks/useBracken'
import styles from '../styles/components.module.css'

interface Props {
  groups: GroupEntry[]
  activeGroup: string | null
  identityPubkey: string
  relayConns: RelayConnection[]
  channels: string[]
  selectedChannel: string
  onSelectGroup: (pubkey: string) => void
  onSelectChannel: (channel: string) => void
  onAddGroupClick: () => void
  onIdentityClick: () => void
}

export function Sidebar({
  groups,
  activeGroup,
  identityPubkey,
  relayConns,
  channels,
  selectedChannel,
  onSelectGroup,
  onSelectChannel,
  onAddGroupClick,
  onIdentityClick,
}: Props) {
  const [expanded, setExpanded] = useState<Set<string>>(
    new Set(activeGroup ? [activeGroup] : []),
  )

  const toggle = (pubkey: string) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(pubkey)) next.delete(pubkey)
      else next.add(pubkey)
      return next
    })
  }

  const connectedCount = relayConns.filter((r) => r.connected).length
  const totalRelays = relayConns.length
  const dotClass =
    connectedCount === totalRelays && totalRelays > 0
      ? styles.connDotGreen
      : connectedCount > 0
        ? styles.connDotAmber
        : styles.connDotRed

  return (
    <div className={styles.sidebar}>
      <div className={styles.sidebarHeader}>
        <FernLogo size={20} />
        <span>Bracken</span>
      </div>

      <div className={styles.groupList}>
        {groups.map((group) => {
          const isExpanded = expanded.has(group.pubkey)
          return (
            <div key={group.pubkey} className={styles.groupEntry}>
              <div
                className={styles.groupName}
                onClick={() => {
                  toggle(group.pubkey)
                  onSelectGroup(group.pubkey)
                }}
              >
                <span
                  className={`${styles.arrow} ${
                    isExpanded ? styles.arrowExpanded : ''
                  }`}
                >
                  ▸
                </span>
                {group.name}
              </div>
              {isExpanded &&
                channels.map((ch) => (
                  <div
                    key={ch}
                    className={`${styles.channelItem} ${
                      activeGroup === group.pubkey && selectedChannel === ch
                        ? styles.channelItemActive
                        : ''
                    }`}
                    onClick={() => {
                      onSelectGroup(group.pubkey)
                      onSelectChannel(ch)
                    }}
                  >
                    # {ch}
                  </div>
                ))}
            </div>
          )
        })}
        <button className={styles.joinBtn} onClick={onAddGroupClick}>
          + Add group
        </button>
      </div>

      <div className={styles.identityRow} onClick={onIdentityClick} style={{ cursor: 'pointer' }}>
        <div className={`${styles.connDot} ${dotClass}`} />
        <span className="mono">{truncateId(identityPubkey)}</span>
      </div>
    </div>
  )
}
