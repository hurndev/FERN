import { useEffect, useState } from 'react'
import { FernLogo } from './FernLogo'
import { IdentitySetup } from './IdentitySetup'
import { fetchGroupPreview, type GroupPreview } from '../fern/relay'
import styles from '../styles/components.module.css'

export interface PendingJoin {
  pubkey: string
  relays: string[]
}

interface Props {
  pendingJoin: PendingJoin
  hasIdentity: boolean
  alreadyMember: boolean
  onImportIdentity: (seed: string) => Promise<void>
  onJoin: (address: string) => Promise<void>
  onSwitchToGroup: (pubkey: string) => void
  onCancel: () => void
}

type LoadPhase = 'loading' | 'preview' | 'noRelays' | 'notFound'
type JoinPhase = 'idle' | 'joining' | 'error'

function truncateKey(key: string, head = 8, tail = 4): string {
  if (key.length <= head + tail + 1) return key
  return `${key.slice(0, head)}\u2026${key.slice(-tail)}`
}

export function InvitePreview({
  pendingJoin,
  hasIdentity,
  alreadyMember,
  onImportIdentity,
  onJoin,
  onSwitchToGroup,
  onCancel,
}: Props) {
  const [loadPhase, setLoadPhase] = useState<LoadPhase>('loading')
  const [preview, setPreview] = useState<GroupPreview | null>(null)
  const [unreachable, setUnreachable] = useState<string[]>([])
  const [extraRelays, setExtraRelays] = useState<string[]>([])
  const [joinPhase, setJoinPhase] = useState<JoinPhase>('idle')
  const [joinError, setJoinError] = useState<string | null>(null)

  const allRelays = [...pendingJoin.relays, ...extraRelays].filter(
    (v, i, a) => a.indexOf(v) === i,
  )

  useEffect(() => {
    let cancelled = false
    if (allRelays.length === 0) {
      setLoadPhase('noRelays')
      setPreview(null)
      setUnreachable([])
      return
    }
    setLoadPhase('loading')
    setPreview(null)
    setJoinError(null)
    setJoinPhase('idle')
    fetchGroupPreview(pendingJoin.pubkey, allRelays).then((result) => {
      if (cancelled) return
      if ('error' in result) {
        setLoadPhase('notFound')
        setUnreachable(result.unreachable)
      } else {
        setLoadPhase('preview')
        setPreview(result)
      }
    })
    return () => {
      cancelled = true
    }
  }, [pendingJoin.pubkey, allRelays.join(',')])

  const handleJoin = async () => {
    setJoinPhase('joining')
    setJoinError(null)
    const address = `fern:${pendingJoin.pubkey}@${allRelays.join(',')}`
    try {
      await onJoin(address)
    } catch (e) {
      setJoinPhase('error')
      setJoinError(String(e))
    }
  }

  const showCard = preview !== null
  const showJoinActions =
    hasIdentity && !alreadyMember && loadPhase === 'preview' && joinPhase === 'idle'
  const showRetryActions = hasIdentity && joinPhase === 'error'
  const showAlreadyMemberActions = hasIdentity && alreadyMember && preview !== null

  return (
    <div className={styles.inviteScreen}>
      <div className={styles.invitePanel}>
        <div className={styles.inviteLogo}>
          <FernLogo size={28} />
        </div>

        <h1 className={styles.inviteTitle}>You've been invited to join a group</h1>
        <p className={styles.inviteSubtitle}>
          Someone shared a FERN group with you. Review the details below, then continue.
        </p>

        {loadPhase === 'loading' && (
          <div className={styles.inviteLoading}>
            <div className={styles.inviteSpinner} />
            <span>Loading group info…</span>
          </div>
        )}

        {loadPhase === 'noRelays' && (
          <div className={styles.inviteError}>
            <p>This invite link has no relay hints.</p>
            <p className={styles.inviteErrorHint}>
              Add at least one relay URL below. The relay must host the group.
            </p>
          </div>
        )}

        {loadPhase === 'notFound' && (
          <div className={styles.inviteError}>
            <p>Could not load group info from any provided relay.</p>
            {unreachable.length > 0 && (
              <p className={styles.inviteErrorHint}>
                Tried: {unreachable.join(', ')}
              </p>
            )}
            <p className={styles.inviteErrorHint}>
              Add a different relay URL below and we'll try again.
            </p>
          </div>
        )}

        {showCard && preview && <GroupCard preview={preview} pubkey={pendingJoin.pubkey} />}

        {joinPhase === 'joining' && (
          <div className={styles.inviteLoading}>
            <div className={styles.inviteSpinner} />
            <span>Joining…</span>
          </div>
        )}

        {joinError && <p className={styles.errorText}>{joinError}</p>}

        {!hasIdentity && loadPhase !== 'loading' && (
          <div className={styles.inviteIdentitySection}>
            <div className={styles.inviteDivider}>
              <span>To accept, create your identity</span>
            </div>
            <IdentitySetup onImport={onImportIdentity} />
          </div>
        )}

        {showJoinActions && (
          <div className={styles.inviteActions}>
            <button className={styles.primaryBtn} onClick={handleJoin}>
              Join group
            </button>
            <button className={styles.secondaryBtn} onClick={onCancel}>
              Cancel
            </button>
          </div>
        )}

        {showRetryActions && (
          <div className={styles.inviteActions}>
            <button className={styles.primaryBtn} onClick={handleJoin}>
              Retry
            </button>
            <button className={styles.secondaryBtn} onClick={onCancel}>
              Cancel
            </button>
          </div>
        )}

        {showAlreadyMemberActions && (
          <div className={styles.inviteAlreadyMember}>
            <p>You're already in this group.</p>
            <div className={styles.inviteActions}>
              <button
                className={styles.primaryBtn}
                onClick={() => onSwitchToGroup(pendingJoin.pubkey)}
              >
                Go to group
              </button>
              <button className={styles.secondaryBtn} onClick={onCancel}>
                Cancel
              </button>
            </div>
          </div>
        )}

        {hasIdentity && loadPhase === 'loading' && (
          <div className={styles.inviteActions}>
            <button className={styles.secondaryBtn} onClick={onCancel}>
              Cancel
            </button>
          </div>
        )}

        {(loadPhase === 'noRelays' || loadPhase === 'notFound') && (
          <RelayInput
            relays={allRelays}
            onAdd={(url) => setExtraRelays((r) => [...r, url])}
            onRemove={(url) =>
              setExtraRelays((r) => r.filter((x) => x !== url))
            }
          />
        )}
      </div>
    </div>
  )
}

function GroupCard({ preview, pubkey }: { preview: GroupPreview; pubkey: string }) {
  return (
    <div className={styles.inviteGroupCard}>
      <div className={styles.inviteGroupHeader}>
        <div className={styles.inviteGroupName}>{preview.name}</div>
        <div
          className={`${styles.inviteBadge} ${
            preview.public ? styles.inviteBadgePublic : styles.inviteBadgePrivate
          }`}
        >
          {preview.public ? 'public' : 'private'}
        </div>
      </div>
      {preview.description.trim() && (
        <div className={styles.inviteGroupDesc}>{preview.description}</div>
      )}
      <div className={styles.inviteGroupMeta}>
        <span>
          {preview.canonicalRelays.length > 0
            ? `${preview.canonicalRelays.length} canonical relay${preview.canonicalRelays.length === 1 ? '' : 's'}`
            : 'No canonical relays listed'}
        </span>
        <span className={styles.inviteGroupPubkey}>
          <span className={styles.inviteGroupMetaLabel}>group</span>
          <code className="mono">{truncateKey(pubkey, 10, 6)}</code>
        </span>
      </div>
      {preview.canonicalRelays.length > 0 && (
        <div className={styles.inviteRelayList}>
          {preview.canonicalRelays.map((r) => (
            <code key={r} className={`mono ${styles.inviteRelayItem}`}>
              {r}
            </code>
          ))}
        </div>
      )}
    </div>
  )
}

function RelayInput({
  relays,
  onAdd,
  onRemove,
}: {
  relays: string[]
  onAdd: (url: string) => void
  onRemove: (url: string) => void
}) {
  const [draft, setDraft] = useState('')

  const handleAdd = () => {
    const url = draft.trim()
    if (!url) return
    const normalized = url.startsWith('ws://') || url.startsWith('wss://') ? url : `wss://${url}`
    onAdd(normalized)
    setDraft('')
  }

  return (
    <div className={styles.inviteRelayInput}>
      {relays.length > 0 && (
        <div className={styles.inviteRelayList}>
          {relays.map((r) => (
            <div key={r} className={styles.inviteRelayItemRow}>
              <code className="mono">{r}</code>
              <button
                className={styles.inviteRelayRemove}
                onClick={() => onRemove(r)}
                title="Remove"
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}
      <div className={styles.inviteRelayAddRow}>
        <input
          className={styles.modalInput}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="wss://relay.example.com"
          spellCheck={false}
          onKeyDown={(e) => {
            if (e.key === 'Enter') handleAdd()
          }}
        />
        <button className={styles.primaryBtn} onClick={handleAdd} disabled={!draft.trim()}>
          Add
        </button>
      </div>
    </div>
  )
}
