import { useState } from 'react'
import { FernLogo } from './FernLogo'
import { generateKeypair } from '../fern/crypto'
import { isValidHex } from '../fern/utils'
import styles from '../styles/components.module.css'

interface Props {
  onImport: (seed: string) => Promise<void>
}

export function IdentitySetup({ onImport }: Props) {
  const [mode, setMode] = useState<'welcome' | 'create' | 'import'>('welcome')
  const [seed, setSeed] = useState('')
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [generatedSeed, setGeneratedSeed] = useState('')

  const handleCreate = async () => {
    setBusy(true)
    setError(null)
    try {
      const kp = generateKeypair()
      setGeneratedSeed(kp.seed)
      setMode('create')
    } catch (e) {
      setError(String(e))
    }
    setBusy(false)
  }

  const handleConfirm = async () => {
    if (!saved || !generatedSeed) return
    setBusy(true)
    setError(null)
    try {
      await onImport(generatedSeed)
    } catch (e) {
      setError(String(e))
    }
    setBusy(false)
  }

  const handleImport = async () => {
    if (!isValidHex(seed, 64)) {
      setError('Private key must be 64-char lowercase hex')
      return
    }
    setBusy(true)
    setError(null)
    try {
      await onImport(seed)
    } catch (e) {
      setError(String(e))
    }
    setBusy(false)
  }

  return (
    <div className={styles.setupScreen}>
      <div className={styles.setupPanel}>
        <div className={styles.setupLogo}>
          <FernLogo size={28} />
        </div>

        {mode === 'welcome' && (
          <>
            <h1 className={styles.setupTitle}>Welcome to Bracken</h1>
            <p className={styles.setupSubtitle}>
              A group chat client for the FERN protocol.
              Your identity is a cryptographic keypair stored locally.
            </p>
            <div className={styles.setupActions}>
              <button className={styles.primaryBtn} onClick={handleCreate} disabled={busy}>
                Create identity
              </button>
              <button className={styles.secondaryBtn} onClick={() => setMode('import')}>
                Import private key
              </button>
            </div>
          </>
        )}

        {mode === 'create' && (
          <>
            <h1 className={styles.setupTitle}>Your private key</h1>
            <p className={styles.setupSubtitle}>
              Save this private key. It cannot be recovered if lost.
            </p>
            <div className={styles.seedBox}>
              <code className="mono">{generatedSeed}</code>
              <button
                className={styles.copyBtn}
                onClick={() => navigator.clipboard.writeText(generatedSeed)}
              >
                Copy
              </button>
            </div>
            <label className={styles.checkboxRow}>
              <input
                type="checkbox"
                checked={saved}
                onChange={(e) => setSaved(e.target.checked)}
              />
              <span>I've saved my private key</span>
            </label>
            <button
              className={styles.primaryBtn}
              onClick={handleConfirm}
              disabled={!saved || busy}
            >
              Enter Bracken
            </button>
            <button className={styles.secondaryBtn} onClick={() => setMode('welcome')}>
              Back
            </button>
          </>
        )}

        {mode === 'import' && (
          <>
            <h1 className={styles.setupTitle}>Import private key</h1>
            <p className={styles.setupSubtitle}>
              Enter your 64-character hex private key.
            </p>
            <input
              className={`${styles.seedInput} mono`}
              value={seed}
              onChange={(e) => setSeed(e.target.value.toLowerCase())}
              placeholder="0000000000000000000000000000000000000000000000000000000000000000"
              maxLength={64}
              spellCheck={false}
            />
            <button className={styles.primaryBtn} onClick={handleImport} disabled={busy || seed.length !== 64}>
              Import
            </button>
            <button className={styles.secondaryBtn} onClick={() => setMode('welcome')}>
              Back
            </button>
          </>
        )}

        {error && <p className={styles.errorText}>{error}</p>}
      </div>
    </div>
  )
}
