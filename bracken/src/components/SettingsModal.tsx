import { useState, useEffect } from 'react'
import { Avatar } from './Avatar'
import styles from '../styles/components.module.css'

interface Props {
  pubkey: string
  privateKey: string
  currentNickname: string | null
  onClose: () => void
  onSetNickname?: (name: string) => Promise<void>
  onLogout?: () => Promise<void>
}

export function SettingsModal({
  pubkey,
  privateKey,
  currentNickname,
  onClose,
  onSetNickname,
  onLogout,
}: Props) {
  const [copied, setCopied] = useState(false)
  const [privateKeyCopied, setPrivateKeyCopied] = useState(false)
  const [nickname, setNickname] = useState(currentNickname ?? '')
  const [showPrivateKey, setShowPrivateKey] = useState(false)
  const [busy, setBusy] = useState(false)

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

  const handleCopyPrivateKey = () => {
    navigator.clipboard.writeText(privateKey)
    setPrivateKeyCopied(true)
    setTimeout(() => setPrivateKeyCopied(false), 1500)
  }

  const handleSetNickname = async () => {
    if (!onSetNickname || !nickname.trim()) return
    setBusy(true)
    try {
      await onSetNickname(nickname.trim())
    } finally {
      setBusy(false)
    }
  }

  const handleLogout = async () => {
    if (!onLogout) return
    setBusy(true)
    try {
      await onLogout()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={styles.profileOverlay} onClick={onClose}>
      <div className={styles.profileModal} onClick={(e) => e.stopPropagation()}>
        <button className={styles.profileClose} onClick={onClose}>✕</button>
        <div className={styles.profileHeader}>
          <div className={styles.profileAvatar}>
            <Avatar value={pubkey} size={48} />
          </div>
          <div className={styles.profileIdentity}>
            <div className={styles.profileName}>Your Identity</div>
            <div className={styles.profileRole}>Settings</div>
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
        <div className={styles.profileField}>
          <span className={styles.profileLabel}>Nickname</span>
          <div className={styles.profileValue}>
            <input
              className={styles.nicknameInput}
              value={nickname}
              onChange={(e) => setNickname(e.target.value)}
              placeholder="Set a display name"
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleSetNickname()
              }}
            />
            <button
              className={styles.profileCopyBtn}
              onClick={handleSetNickname}
              disabled={busy || !nickname.trim() || nickname === currentNickname}
            >
              {busy ? '...' : 'Set'}
            </button>
          </div>
        </div>
        <div className={styles.profileField}>
          <span className={styles.profileLabel}>Private Key</span>
          <div className={styles.profileValue}>
            <span className={styles.privateKeyValue}>
              {showPrivateKey ? privateKey : '********************************'}
            </span>
            <button
              className={styles.iconBtn}
              onClick={() => setShowPrivateKey((value) => !value)}
              title={showPrivateKey ? 'Hide private key' : 'Show private key'}
              aria-label={showPrivateKey ? 'Hide private key' : 'Show private key'}
            >
              <span
                aria-hidden="true"
                className={`${styles.eyeIcon} ${!showPrivateKey ? styles.eyeIconMuted : ''}`}
              />
            </button>
            <button className={styles.profileCopyBtn} onClick={handleCopyPrivateKey}>
              {privateKeyCopied ? 'Copied' : 'Copy'}
            </button>
          </div>
        </div>
        <div className={styles.logoutSection}>
          <button className={styles.dangerBtn} onClick={handleLogout} disabled={busy}>
            Log out
          </button>
        </div>
      </div>
    </div>
  )
}
