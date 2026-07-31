import { useState, useEffect } from 'react'
import { truncateId } from '../fern/utils'
import { Avatar } from './Avatar'
import { useDefiniteOverlayClick } from '../hooks/useDefiniteOverlayClick'
import styles from '../styles/components.module.css'

interface Props {
  pubkey: string
  nickname: string | null
  isManager: boolean
  isMod: boolean
  isBanned?: boolean
  isMember?: boolean
  viewerIsManager?: boolean
  viewerIsMod?: boolean
  viewerPubkey?: string
  onClose: () => void
  onAdminAction?: (type: string, targetPubkey: string, extra?: Record<string, unknown>) => Promise<void>
}

export function ProfilePopup({
  pubkey, nickname, isManager, isMod, isBanned = false, isMember = true,
  viewerIsManager = false, viewerIsMod = false, viewerPubkey = '',
  onClose, onAdminAction,
}: Props) {
  const overlayHandlers = useDefiniteOverlayClick(onClose)
  const [copied, setCopied] = useState(false)
  const [acting, setActing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const isSelf = viewerPubkey === pubkey

  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleEsc)
    return () => document.removeEventListener('keydown', handleEsc)
  }, [onClose])

  const handleCopy = () => {
    navigator.clipboard.writeText(pubkey)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  const handleAction = async (type: string) => {
    if (!onAdminAction) return
    setActing(true)
    setError(null)
    try {
      await onAdminAction(type, pubkey)
      onClose()
    } catch (err) {
      setError(String(err))
    } finally {
      setActing(false)
    }
  }

  const viewerCanModerate = viewerIsManager || viewerIsMod
  const showAdminActions = viewerCanModerate && !isSelf
  const showRoleActions = viewerIsManager && !isSelf

  return (
    <div className={styles.profileOverlay} {...overlayHandlers}>
      <div className={styles.profileModal}>
        <button className={styles.profileClose} onClick={onClose}>✕</button>
        <div className={styles.profileHeader}>
          <div className={styles.profileAvatar}>
            <Avatar value={pubkey} size={48} />
          </div>
          <div className={styles.profileIdentity}>
            <div className={styles.profileName + ((isManager || isMod) && isMember ? ' ' + styles.profileNameMod : '')}>
              {nickname ?? truncateId(pubkey)}
              {isSelf && <span className={styles.memberModTag}> (You)</span>}
            </div>
            <div className={styles.profileRole}>
              {isMember ? (isManager ? 'Manager' : isMod ? 'Moderator' : 'Member') : 'Not in group'}
              {isBanned && <span className={styles.profileBanned}> · Banned</span>}
            </div>
          </div>
        </div>
        <div className={styles.profileField}>
          <span className={styles.profileLabel}>Public Key</span>
          <div className={styles.profileValue}>
            <span className={styles.profilePubkey}>{pubkey}</span>
            <button className={styles.profileCopyBtn} onClick={handleCopy}>
              {copied ? 'Copied' : 'Copy'}
            </button>
          </div>
        </div>
        {nickname && (
          <div className={styles.profileField}>
            <span className={styles.profileLabel}>Nickname</span>
            <span className={styles.profileValue}>{nickname}</span>
          </div>
        )}
        {showAdminActions && (
          <div className={styles.modActions}>
            <span className={styles.profileLabel}>Moderation</span>
            <div className={styles.modActionBtns}>
              <button
                className={styles.modActionBtn}
                onClick={() => handleAction('kick')}
                disabled={acting}
              >
                Kick
              </button>
              {isBanned ? (
                <button
                  className={`${styles.modActionBtn} ${styles.modActionBan}`}
                  onClick={() => handleAction('unban')}
                  disabled={acting}
                >
                  Unban
                </button>
              ) : (
                <button
                  className={`${styles.modActionBtn} ${styles.modActionBan}`}
                  onClick={() => handleAction('ban')}
                  disabled={acting}
                >
                  Ban
                </button>
              )}
            </div>
            {showRoleActions && (
              <div className={styles.modActionBtns}>
                {isManager ? (
                  <button
                    className={styles.modActionBtn}
                    onClick={() => handleAction('chat.manager_remove')}
                    disabled={acting}
                  >
                    Remove manager
                  </button>
                ) : (
                  <button
                    className={styles.modActionBtn}
                    onClick={() => handleAction('chat.manager_add')}
                    disabled={acting}
                  >
                    Make manager
                  </button>
                )}
                {isMod ? (
                  <button
                    className={styles.modActionBtn}
                    onClick={() => handleAction('chat.mod_remove')}
                    disabled={acting}
                  >
                    Remove mod
                  </button>
                ) : (
                  <button
                    className={styles.modActionBtn}
                    onClick={() => handleAction('chat.mod_add')}
                    disabled={acting}
                  >
                    Make mod
                  </button>
                )}
              </div>
            )}
            {error && <div className={styles.formError}>{error}</div>}
          </div>
        )}
      </div>
    </div>
  )
}
