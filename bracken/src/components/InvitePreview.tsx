import { useEffect, useMemo, useState } from 'react'
import { FernLogo } from './FernLogo'
import { IdentitySetup } from './IdentitySetup'
import { fetchGroupPreview, type GroupPreview } from '../fern/validator'
import styles from '../styles/components.module.css'

export interface PendingJoin {
  pubkey: string
  validators: string[]
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

type LoadPhase = 'loading' | 'preview' | 'noValidators' | 'notFound'
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
  const [extraValidators, setExtraValidators] = useState<string[]>([])
  const [joinPhase, setJoinPhase] = useState<JoinPhase>('idle')
  const [joinError, setJoinError] = useState<string | null>(null)

  const allValidators = useMemo(
    () => [...pendingJoin.validators, ...extraValidators].filter((v, i, a) => a.indexOf(v) === i),
    [pendingJoin.validators, extraValidators],
  )

  useEffect(() => {
    let cancelled = false
    const loadPreview = async () => {
      await Promise.resolve()
      if (cancelled) return
      if (allValidators.length === 0) {
        setLoadPhase('noValidators')
        setPreview(null)
        setUnreachable([])
        return
      }
      setLoadPhase('loading')
      setPreview(null)
      setJoinError(null)
      setJoinPhase('idle')
      const result = await fetchGroupPreview(pendingJoin.pubkey, allValidators)
      if (cancelled) return
      if ('error' in result) {
        setLoadPhase('notFound')
        setUnreachable(result.unreachable)
      } else {
        setLoadPhase('preview')
        setPreview(result)
      }
    }
    void loadPreview()
    return () => {
      cancelled = true
    }
  }, [pendingJoin.pubkey, allValidators])

  const handleJoin = async () => {
    setJoinPhase('joining')
    setJoinError(null)
    const address = `fern:${pendingJoin.pubkey}@${allValidators.join(',')}`
    try {
      await onJoin(address)
      onCancel()
    } catch (e) {
      setJoinPhase('error')
      setJoinError(String(e))
    }
  }

  useEffect(() => {
    if (!alreadyMember || !preview) return
    const timer = setTimeout(() => {
      onSwitchToGroup(pendingJoin.pubkey)
      onCancel()
    }, 600)
    return () => clearTimeout(timer)
  }, [alreadyMember, preview, pendingJoin.pubkey, onSwitchToGroup, onCancel])

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

        {loadPhase === 'noValidators' && (
          <div className={styles.inviteError}>
            <p>This invite link has no validator endpoints.</p>
            <p className={styles.inviteErrorHint}>
              Add at least one current validator URL below. It must host the complete group history.
            </p>
          </div>
        )}

        {loadPhase === 'notFound' && (
          <div className={styles.inviteError}>
            <p>Could not load group info from any provided validator.</p>
            {unreachable.length > 0 && (
              <p className={styles.inviteErrorHint}>
                Tried: {unreachable.join(', ')}
              </p>
            )}
            <p className={styles.inviteErrorHint}>
              Add a different validator URL below and we'll try again.
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
            <p>You're already in this group. Opening it now…</p>
          </div>
        )}

        {hasIdentity && loadPhase === 'loading' && (
          <div className={styles.inviteActions}>
            <button className={styles.secondaryBtn} onClick={onCancel}>
              Cancel
            </button>
          </div>
        )}

        {(loadPhase === 'noValidators' || loadPhase === 'notFound') && (
          <ValidatorInput
            validators={allValidators}
            onAdd={(url) => setExtraValidators((current) => [...current, url])}
            onRemove={(url) =>
              setExtraValidators((current) => current.filter((value) => value !== url))
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
          {preview.canonicalValidators.length > 0
            ? `${preview.canonicalValidators.length} validator${preview.canonicalValidators.length === 1 ? '' : 's'}`
            : 'No validators listed'}
        </span>
        <span className={styles.inviteGroupPubkey}>
          <span className={styles.inviteGroupMetaLabel}>group</span>
          <code className="mono">{truncateKey(pubkey, 10, 6)}</code>
        </span>
      </div>
      {preview.canonicalValidators.length > 0 && preview.canonicalValidators.length < 4 && (
        <div className={styles.smallSetWarning} role="alert">
          Warning: this group requires all {preview.canonicalValidators.length} validator{preview.canonicalValidators.length === 1 ? '' : 's'}
          {' '}to participate. Any unavailable validator halts consensus.
        </div>
      )}
      {preview.canonicalValidators.length > 0 && (
        <div className={styles.inviteValidatorList}>
          {preview.canonicalValidators.map((url) => (
            <code key={url} className={`mono ${styles.inviteValidatorItem}`}>
              {url}
            </code>
          ))}
        </div>
      )}
    </div>
  )
}

function ValidatorInput({
  validators,
  onAdd,
  onRemove,
}: {
  validators: string[]
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
    <div className={styles.inviteValidatorInput}>
      {validators.length > 0 && (
        <div className={styles.inviteValidatorList}>
          {validators.map((url) => (
            <div key={url} className={styles.inviteValidatorItemRow}>
              <code className="mono">{url}</code>
              <button
                className={styles.inviteValidatorRemove}
                onClick={() => onRemove(url)}
                title="Remove"
              >
                ✕
              </button>
            </div>
          ))}
        </div>
      )}
      <div className={styles.inviteValidatorAddRow}>
        <input
          className={styles.modalInput}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="wss://validator.example.com"
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
