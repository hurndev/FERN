import { useState } from 'react'
import styles from '../styles/components.module.css'

interface Props {
  onJoin: (address: string) => Promise<void>
  onClose: () => void
}

export function JoinModal({ onJoin, onClose }: Props) {
  const [address, setAddress] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [progress, setProgress] = useState<string[]>([])

  const handleSubmit = async () => {
    if (!address.trim()) return
    setBusy(true)
    setError(null)
    setProgress([])
    try {
      setProgress((p) => [...p, 'Connecting to relay…'])
      await onJoin(address.trim())
      setProgress((p) => [...p, 'Fetching history…', 'Ready.'])
      setTimeout(onClose, 500)
    } catch (e) {
      setError(String(e))
    }
    setBusy(false)
  }

  return (
    <div className={styles.modalOverlay} onClick={onClose}>
      <div className={styles.modal} onClick={(e) => e.stopPropagation()}>
        <div className={styles.modalTitle}>Join a group</div>
        <input
          className={styles.modalInput}
          value={address}
          onChange={(e) => setAddress(e.target.value)}
          placeholder="fern:<pubkey>@<relay>,<relay>"
          disabled={busy}
          onKeyDown={(e) => {
            if (e.key === 'Enter') handleSubmit()
            if (e.key === 'Escape') onClose()
          }}
        />
        {progress.map((line, i) => (
          <div key={i} className={styles.progressLine}>
            <span className={styles.progressCheck}>✓</span>
            {line}
          </div>
        ))}
        {error && <p className={styles.errorText}>{error}</p>}
        <button
          className={styles.primaryBtn}
          onClick={handleSubmit}
          disabled={busy || !address.trim()}
        >
          Join
        </button>
      </div>
    </div>
  )
}
