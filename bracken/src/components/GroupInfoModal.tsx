import { useMemo, useState } from 'react'
import { useDefiniteOverlayClick } from '../hooks/useDefiniteOverlayClick'
import styles from '../styles/components.module.css'

interface Props {
  name: string
  pubkey: string
  description: string
  validators: string[]
  validatorCount: number
  onViewChain?: () => void
  onLeaveGroup?: () => Promise<void> | void
  onClose: () => void
}

export function GroupInfoModal({
  name, pubkey, description, validators, validatorCount, onViewChain, onLeaveGroup, onClose,
}: Props) {
  const [copied, setCopied] = useState<string | null>(null)
  const [leaving, setLeaving] = useState(false)
  const overlayHandlers = useDefiniteOverlayClick(onClose)

  const inviteLink = useMemo(() => {
    const validatorQuery = validators.length > 0 ? `&validators=${validators.join(',')}` : ''
    return `${window.location.origin}/?group=${pubkey}${validatorQuery}`
  }, [pubkey, validators])

  const copy = (label: string, value: string) => {
    navigator.clipboard.writeText(value)
    setCopied(label)
    setTimeout(() => setCopied(null), 1500)
  }

  return (
    <div className={styles.modalOverlay} {...overlayHandlers}>
      <div className={styles.groupInfoModal}>
        <div className={styles.groupInfoHeader}>
          <div>
            <div className={styles.groupInfoTitle}>{name}</div>
            <div className={styles.groupInfoSubtitle}>Group information</div>
          </div>
          <button className={styles.drawerClose} onClick={onClose}>✕</button>
        </div>

        {validatorCount > 0 && validatorCount < 4 && (
          <div className={styles.smallSetWarning} role="alert">
            Warning: this group uses unanimous small-set mode. All {validatorCount}
            {' '}validators must participate, so any unavailable validator halts consensus.
          </div>
        )}

        <div className={styles.groupInfoField}>
          <span className={styles.profileLabel}>Description</span>
          <span className={styles.groupInfoText}>
            {description.trim() || 'No description set.'}
          </span>
        </div>

        <div className={styles.groupInfoField}>
          <span className={styles.profileLabel}>Public Key</span>
          <div className={styles.groupInfoValue}>
            <span className={styles.groupInfoMono}>{pubkey}</span>
            <button className={styles.groupInfoCopyBtn} onClick={() => copy('pubkey', pubkey)}>
              {copied === 'pubkey' ? 'Copied' : 'Copy'}
            </button>
          </div>
        </div>

        <div className={styles.groupInfoField}>
          <span className={styles.profileLabel}>Invite Link</span>
          <div className={styles.groupInfoValue}>
            <span className={styles.groupInfoMono}>{inviteLink}</span>
            <button className={styles.groupInfoCopyBtn} onClick={() => copy('invite', inviteLink)}>
              {copied === 'invite' ? 'Copied' : 'Copy'}
            </button>
          </div>
        </div>

        <div className={styles.groupInfoField}>
          <span className={styles.profileLabel}>Validators</span>
          {validators.length > 0 ? (
            <div className={styles.groupInfoValidatorList}>
              {validators.map((validator) => (
                <span key={validator} className={styles.groupInfoMono}>{validator}</span>
              ))}
            </div>
          ) : (
            <span className={styles.groupInfoText}>No validator endpoints are configured.</span>
          )}
        </div>

        {onViewChain && (
          <button className={styles.groupInfoActionBtn} onClick={onViewChain}>
            View finalized chain
          </button>
        )}

        {onLeaveGroup && (
          <button
            className={styles.dangerBtn}
            disabled={leaving}
            onClick={async () => {
              setLeaving(true)
              try {
                await onLeaveGroup()
              } finally {
                setLeaving(false)
              }
            }}
          >
            {leaving ? 'Leaving...' : 'Leave group'}
          </button>
        )}
      </div>
    </div>
  )
}
