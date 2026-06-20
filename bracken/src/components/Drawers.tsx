import { useState, useCallback } from 'react'
import { truncateId, relativeTime } from '../fern/utils'
import type { GroupState } from '../fern/state'
import type { RelayConnection } from '../hooks/useBracken'
import { ProfilePopup } from './ProfilePopup'
import { Avatar } from './Avatar'
import styles from '../styles/components.module.css'

interface MemberDrawerProps {
  state: GroupState
  nicknames: Map<string, string>
  viewerPubkey?: string
  onClose: () => void
  onAdminAction?: (type: string, targetPubkey: string, extra?: Record<string, unknown>) => Promise<void>
}

export function MemberDrawer({ state, nicknames, viewerPubkey = '', onClose, onAdminAction }: MemberDrawerProps) {
  const [profile, setProfile] = useState<string | null>(null)

  const openProfile = useCallback((pubkey: string) => {
    setProfile(pubkey)
  }, [])

  const admins = [...state.joined].filter((pk) => state.admins.has(pk)).sort()
  const nonAdmins = [...state.joined].filter((pk) => !state.admins.has(pk)).sort()
  const ordered = [...admins, ...nonAdmins]
  const banned = [...state.banned.entries()].sort((a, b) => a[0].localeCompare(b[0]))

  return (
    <>
      <div className={styles.drawerOverlay} onClick={onClose} />
      <div className={styles.drawer}>
        <div className={styles.drawerHeader}>
          <span className={styles.drawerTitle}>Members ({state.joined.size})</span>
          <button className={styles.drawerClose} onClick={onClose}>✕</button>
        </div>
        <div className={styles.drawerBody}>
          {ordered.map((pubkey) => {
            const isAdmin = state.admins.has(pubkey)
            const nick = nicknames.get(pubkey)
            return (
              <div
                key={pubkey}
                className={styles.memberRow}
                onClick={() => openProfile(pubkey)}
              >
                <Avatar value={pubkey} size={24} />
                <span className={`${styles.memberPubkey} ${isAdmin ? styles.memberPubkeyMod : ''}`}>
                  {nick ?? truncateId(pubkey)}
                  {pubkey === viewerPubkey && ' (You)'}
                </span>
              </div>
            )
          })}
          {banned.length > 0 && (
            <>
              <div
                style={{
                  padding: '8px 16px 4px',
                  fontSize: 'var(--text-xs)',
                  color: 'var(--text-ghost)',
                  textTransform: 'uppercase',
                }}
              >
                Banned
              </div>
              {banned.map(([pubkey, entry]) => (
                <div
                  key={pubkey}
                  className={styles.memberRow}
                  onClick={() => openProfile(pubkey)}
                >
                  <span className={styles.memberPubkey}>
                    {nicknames.get(pubkey) ?? truncateId(pubkey)}
                    {pubkey === viewerPubkey && ' (You)'}
                  </span>
                  <span className={styles.drawerItemSub}>
                    {entry.reason || 'Banned'}
                    {entry.until && ` · until ${relativeTime(entry.until)}`}
                  </span>
                </div>
              ))}
            </>
          )}
        </div>
      </div>
      {profile && (
        <ProfilePopup
          pubkey={profile}
          nickname={nicknames.get(profile) ?? null}
          isAdmin={state.admins.has(profile)}
          isBanned={state.banned.has(profile)}
          isMember={state.joined.has(profile)}
          viewerIsAdmin={state.admins.has(viewerPubkey)}
          viewerPubkey={viewerPubkey}
          onClose={() => setProfile(null)}
          onAdminAction={onAdminAction}
        />
      )}
    </>
  )
}

interface RelayDrawerProps {
  relayConns: RelayConnection[]
  onClose: () => void
}

export function RelayDrawer({ relayConns, onClose }: RelayDrawerProps) {
  return (
    <>
      <div className={styles.drawerOverlay} onClick={onClose} />
      <div className={styles.drawer}>
        <div className={styles.drawerHeader}>
          <span className={styles.drawerTitle}>Relays</span>
          <button className={styles.drawerClose} onClick={onClose}>✕</button>
        </div>
        <div className={styles.drawerBody}>
          {relayConns.map((conn, i) => (
            <div key={i} className={styles.drawerItem}>
              <span className="mono" style={{ fontSize: 11, wordBreak: 'break-all' }}>
                {conn.url}
              </span>
              <span
                className={styles.drawerItemSub}
                style={{ color: conn.connected ? 'var(--accent)' : 'var(--danger)' }}
              >
                {conn.connected ? 'Connected' : 'Not connected'}
              </span>
              {conn.pubkey && (
                <span className="mono" style={{ fontSize: 11, color: 'var(--text-ghost)' }}>
                  {truncateId(conn.pubkey)}
                </span>
              )}
            </div>
          ))}
        </div>
      </div>
    </>
  )
}
