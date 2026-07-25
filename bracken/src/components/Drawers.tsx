import { useState, useCallback, useEffect, type ReactNode } from 'react'
import { truncateId, relativeTime } from '../fern/utils'
import type { GroupState } from '../fern/state'
import type { ValidatorSet } from '../fern/bft'
import { isSmallUnanimousValidatorSet, validatorQuorum } from '../fern/bft'
import type { ValidatorStatus } from '../fern/validator'
import type { ValidatorConnection } from '../hooks/useBracken'
import { useDefiniteOverlayClick } from '../hooks/useDefiniteOverlayClick'
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

interface ValidatorDrawerProps {
  validatorConns: ValidatorConnection[]
  validatorSet?: ValidatorSet | null
  onFetchStatus?: (url: string) => Promise<ValidatorStatus | null>
  onClose: () => void
}

function FaultTolerancePanel({
  validatorSet, validatorConns, connected,
}: { validatorSet: ValidatorSet | null; validatorConns: ValidatorConnection[]; connected: number }) {
  if (!validatorSet) {
    return (
      <div className={`${styles.ftPanel} ${styles.ftSync}`}>
        <div className={styles.ftHead}>
          <span className={styles.ftDot} />
          <span className={styles.ftWord}>Verifying</span>
        </div>
      </div>
    )
  }

  const n = validatorSet.validators.length
  const f = validatorSet.fault_tolerance
  const quorum = validatorQuorum(validatorSet)
  const small = isSmallUnanimousValidatorSet(validatorSet)
  const halted = connected < quorum
  const degraded = !halted && connected < n
  const variant = halted ? styles.ftHalted : degraded ? styles.ftDegraded : styles.ftOk
  const word = halted ? 'Halted' : degraded ? 'Degraded' : 'Operational'
  const down = n - connected
  const headroom = Math.max(0, connected - quorum)

  let note: ReactNode
  if (halted) {
    note = <><strong>Below quorum</strong> — consensus halted, no new blocks will finalize.</>
  } else if (small) {
    note = <>Unanimous mode (<strong>f = 0</strong>) — every validator must sign each block; no Byzantine fault tolerance.</>
  } else if (degraded) {
    note = headroom === 0
      ? <>{down} of {n} validators offline — running at exact quorum ({quorum}). One more going offline halts block finalization. Tolerates <strong>{f}</strong> Byzantine.</>
      : <>{down} of {n} validators offline — blocks still finalize. <strong>{headroom}</strong> more can go offline before consensus halts. Tolerates <strong>{f}</strong> Byzantine.</>
  } else {
    note = <>Tolerates <strong>{f}</strong> Byzantine with a {quorum}-vote quorum.</>
  }

  const connByUrl = new Map(validatorConns.map((c) => [c.url, c.connected]))
  const segments = validatorSet.validators
    .map((v) => ({ url: v.url, on: Boolean(connByUrl.get(v.url)) }))
    .sort((a, b) => Number(b.on) - Number(a.on))

  return (
    <div className={`${styles.ftPanel} ${variant}`}>
      <div className={styles.ftHead}>
        <span className={styles.ftDot} />
        <span className={styles.ftWord}>{word}</span>
        <span className={styles.ftEpoch}>epoch {validatorSet.epoch}</span>
      </div>

      <div className={styles.ftBar}>
        {segments.map((seg) => (
          <span
            key={seg.url}
            className={`${styles.ftSeg} ${seg.on ? styles.ftSegOn : ''}`}
            title={`${seg.url} — ${seg.on ? 'connected' : 'down'}`}
          />
        ))}
        <span className={styles.ftTick} style={{ left: `${(quorum / n) * 100}%` }} />
        <span className={styles.ftTickLabel} style={{ left: `${(quorum / n) * 100}%` }}>quorum</span>
      </div>

      <div className={styles.ftNote}>{note}</div>
    </div>
  )
}

function hostOf(url: string): string {
  try { return new URL(url).host } catch { return url }
}

function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n < 1024) return `${n} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let v = n / 1024
  let i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(v >= 10 ? 0 : 1)} ${units[i]}`
}

function ValidatorInfoPopup({
  conn, onFetchStatus, onClose,
}: {
  conn: ValidatorConnection
  onFetchStatus?: (url: string) => Promise<ValidatorStatus | null>
  onClose: () => void
}) {
  const overlayHandlers = useDefiniteOverlayClick(onClose)
  const [fetched, setFetched] = useState<ValidatorStatus | undefined>(undefined)
  const [loading, setLoading] = useState(() => Boolean(onFetchStatus))
  const [tried, setTried] = useState(false)
  const [copied, setCopied] = useState<string | null>(null)
  const status = fetched ?? conn.status
  const stale = tried && !fetched && Boolean(conn.status)

  useEffect(() => {
    if (!onFetchStatus) return
    let alive = true
    onFetchStatus(conn.url).then((st) => {
      if (!alive) return
      setFetched(st ?? undefined)
      setTried(true)
      setLoading(false)
    })
    return () => { alive = false }
  }, [conn.url, onFetchStatus])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const copy = (key: string, value: string) => {
    navigator.clipboard.writeText(value)
    setCopied(key)
    setTimeout(() => setCopied(null), 1500)
  }

  const connWord = conn.connected ? 'Connected' : conn.reconnecting ? 'Reconnecting' : 'Offline'
  const connColor = conn.connected ? 'var(--accent)' : conn.reconnecting ? 'var(--gap)' : 'var(--danger)'
  const displayName = conn.name && conn.name !== hostOf(conn.url) ? conn.name : hostOf(conn.url)

  const dotClass = conn.connected ? styles.valDotOn
    : conn.reconnecting ? styles.valDotRecon : styles.valDotOff

  const copyBtn = (key: string, value: string) => (
    <button className={styles.valCopyBtn} onClick={() => copy(key, value)}>
      {copied === key ? 'Copied' : 'Copy'}
    </button>
  )

  const infoRow = (label: string, value: ReactNode, copyKey?: string, copyVal?: string) => (
    <div className={styles.valInfoRow}>
      <span className={styles.valInfoLabel}>{label}</span>
      <span className={styles.valInfoValue}>{value}</span>
      {copyKey && copyVal && copyBtn(copyKey, copyVal)}
    </div>
  )

  return (
    <div className={styles.profileOverlay} {...overlayHandlers}>
      <div className={styles.valModal}>
        <button className={styles.profileClose} onClick={onClose}>✕</button>

        <div className={styles.valModalHead}>
          <div className={styles.valModalAvatar}>
            <Avatar value={conn.pubkey || conn.url} size={40} />
          </div>
          <div className={styles.valModalIdentity}>
            <div className={styles.valModalName}>{displayName}</div>
            <div className={styles.valModalStatus}>
              <span className={`${styles.valDot} ${dotClass}`} />
              <span style={{ color: connColor }}>{connWord}</span>
            </div>
          </div>
        </div>

        <div className={styles.valModalBody}>
          <div className={styles.valCard}>
            <div className={styles.valSectionHeading}>Connection</div>
            {conn.pubkey ? (
              infoRow('Public key', <span className={styles.valMono}>{truncateId(conn.pubkey, 22)}</span>, 'pk', conn.pubkey)
            ) : (
              infoRow('Public key', <span className={styles.valMuted}>Not yet known</span>)
            )}
            {infoRow('Endpoint', <span className={styles.valMono} title={conn.url}>{conn.url}</span>, 'url', conn.url)}
            {conn.name && conn.name !== hostOf(conn.url) && (
              infoRow('Operator', conn.name)
            )}
          </div>

          <div className={styles.valCard}>
            <div className={styles.valSectionHeading}>Status</div>
            {status ? (
              <>
                {infoRow('Height', status.height.toLocaleString())}
                {infoRow('Epoch', status.epoch)}
                {infoRow('Validators', <>{status.validator_set.validators.length}<span className={styles.valSub}> · quorum {validatorQuorum(status.validator_set)}</span></>)}
                {infoRow('History', <>{formatBytes(status.logical_bytes)}<span className={styles.valSub}> · {status.logical_bytes.toLocaleString()} bytes</span></>)}
                {infoRow('Chain ID', <span className={styles.valMono}>{truncateId(status.chain_id, 16)}</span>, 'chain', status.chain_id)}
                {stale && (
                  <div className={styles.valNoteWarn}>Showing last known status — validator unreachable.</div>
                )}
              </>
            ) : (
              <div className={styles.valNoteMuted}>
                {loading ? 'Fetching signed status…' : 'No signed status available.'}
              </div>
            )}
          </div>

          {status && (
            <div className={styles.valCard}>
              <div className={styles.valSectionHeading}>Roots</div>
              {infoRow('Block', <span className={styles.valMono}>{truncateId(status.block_hash, 16)}</span>, 'block', status.block_hash)}
              {infoRow('History', <span className={styles.valMono}>{truncateId(status.history_root, 16)}</span>, 'hist', status.history_root)}
              {infoRow('State', <span className={styles.valMono}>{truncateId(status.state_root, 16)}</span>, 'state', status.state_root)}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export function ValidatorDrawer({
  validatorConns, validatorSet = null, onFetchStatus, onClose,
}: ValidatorDrawerProps) {
  const connected = validatorConns.filter((c) => c.connected).length
  const [infoUrl, setInfoUrl] = useState<string | null>(null)
  const infoConn = infoUrl ? validatorConns.find((c) => c.url === infoUrl) ?? null : null
  return (
    <>
      <div className={styles.drawerOverlay} onClick={onClose} />
      <div className={styles.drawer}>
        <div className={styles.drawerHeader}>
          <span className={styles.drawerTitle}>Validators</span>
          <button className={styles.drawerClose} onClick={onClose}>✕</button>
        </div>
        <div className={styles.drawerBody}>
          <FaultTolerancePanel
            validatorSet={validatorSet}
            validatorConns={validatorConns}
            connected={connected}
          />
          {validatorConns.map((conn) => (
            <div
              key={conn.url}
              className={styles.valRow}
              title={`View ${conn.url}`}
              onClick={() => setInfoUrl(conn.url)}
            >
              <span
                className={`${styles.valDot} ${
                  conn.connected ? styles.valDotOn
                    : conn.reconnecting ? styles.valDotRecon
                      : styles.valDotOff
                }`}
              />
              <div className={styles.valMain}>
                <span className={styles.valUrl}>{conn.url}</span>
                {conn.pubkey && (
                  <span className={styles.valKey}>{truncateId(conn.pubkey)}</span>
                )}
              </div>
              <span
                className={`${styles.valStatus} ${
                  conn.connected ? styles.valStatusOn
                    : conn.reconnecting ? styles.valStatusRecon
                      : styles.valStatusOff
                }`}
              >
                {conn.connected ? 'connected' : conn.reconnecting ? 'reconnecting' : 'offline'}
              </span>
            </div>
          ))}
        </div>
      </div>
      {infoConn && (
        <ValidatorInfoPopup
          conn={infoConn}
          onFetchStatus={onFetchStatus}
          onClose={() => setInfoUrl(null)}
        />
      )}
    </>
  )
}
