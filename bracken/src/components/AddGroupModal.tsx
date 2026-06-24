import { useState, useEffect } from 'react'
import { DEFAULT_RELAY_HINTS } from '../fern/config'
import { useDefiniteOverlayClick } from '../hooks/useDefiniteOverlayClick'
import styles from '../styles/components.module.css'

interface Props {
  onJoin: (address: string) => Promise<void>
  onCreate: (
    name: string,
    relays: string[],
    options?: { description?: string; public?: boolean },
  ) => Promise<{ ok: number; total: number; error?: string }>
  onClose: () => void
  initialAddress?: string
  initialError?: string | null
}

function normalizeRelay(url: string): string {
  const u = url.trim()
  if (!u) return ''
  if (!u.startsWith('ws://') && !u.startsWith('wss://')) {
    return `ws://${u}`
  }
  return u
}

function parseRelays(input: string): string[] {
  return [
    ...new Set(
      input
        .split(/[\s,]+/)
        .map(normalizeRelay)
        .filter(Boolean),
    ),
  ]
}

export function AddGroupModal({ onJoin, onCreate, onClose, initialAddress, initialError }: Props) {
  const overlayHandlers = useDefiniteOverlayClick(onClose)
  const [mode, setMode] = useState<'join' | 'create'>('join')

  const [address, setAddress] = useState(initialAddress ?? '')
  const [joinBusy, setJoinBusy] = useState(false)
  const [joinError, setJoinError] = useState<string | null>(initialError ?? null)
  const [joinProgress, setJoinProgress] = useState<string[]>([])

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [relays, setRelays] = useState(DEFAULT_RELAY_HINTS.join(', '))
  const [isPublic, setIsPublic] = useState(true)
  const [createBusy, setCreateBusy] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const [createProgress, setCreateProgress] = useState<string[]>([])

  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleEsc)
    return () => document.removeEventListener('keydown', handleEsc)
  }, [onClose])

  const busy = joinBusy || createBusy

  const switchMode = (m: 'join' | 'create') => {
    if (busy || m === mode) return
    setMode(m)
    setJoinError(null)
    setJoinProgress([])
    setCreateError(null)
    setCreateProgress([])
  }

  const handleJoin = async () => {
    if (!address.trim() || joinBusy) return
    setJoinBusy(true)
    setJoinError(null)
    setJoinProgress([])
    try {
      setJoinProgress((p) => [...p, 'Connecting to relay…'])
      await onJoin(address.trim())
      setJoinProgress((p) => [...p, 'Fetching history…', 'Ready.'])
      setTimeout(onClose, 500)
    } catch (e) {
      setJoinError(String(e))
    }
    setJoinBusy(false)
  }

  const parsedRelays = parseRelays(relays)
  const canCreate = name.trim().length > 0 && parsedRelays.length > 0 && !createBusy
  const canJoin = address.trim().length > 0 && !joinBusy

  const handleCreate = async () => {
    if (!canCreate) return
    setCreateBusy(true)
    setCreateError(null)
    setCreateProgress([])
    try {
      setCreateProgress((p) => [...p, 'Generating group keypair…'])
      setCreateProgress((p) => [...p, 'Publishing genesis to relays…'])
      const result = await onCreate(
        name.trim(),
        parsedRelays,
        { description: description.trim(), public: isPublic },
      )
      if (result.total === 0) {
        throw new Error('No relays configured.')
      }
      if (result.ok === 0) {
        throw new Error(result.error ?? 'No relay accepted the genesis event.')
      }
      const line =
        result.ok === result.total
          ? `Published to ${result.ok}/${result.total} relays.`
          : `Published to ${result.ok}/${result.total} relays. ${result.error ?? ''}`
      setCreateProgress((p) => [...p, line, 'Ready.'])
      setTimeout(onClose, 800)
    } catch (e) {
      setCreateError(String(e))
    }
    setCreateBusy(false)
  }

  return (
    <div className={styles.modalOverlay} {...overlayHandlers}>
      <div className={styles.modal}>
        <div className={styles.modalHeader}>
          <div className={styles.modalTitle}>Add a group</div>
          <button className={styles.drawerClose} onClick={onClose}>✕</button>
        </div>

        <div className={styles.modalTabs}>
          <button
            className={`${styles.modalTab} ${mode === 'join' ? styles.modalTabActive : ''}`}
            onClick={() => switchMode('join')}
            disabled={busy}
          >
            Join
          </button>
          <button
            className={`${styles.modalTab} ${mode === 'create' ? styles.modalTabActive : ''}`}
            onClick={() => switchMode('create')}
            disabled={busy}
          >
            Create
          </button>
        </div>

        {mode === 'join' ? (
          <>
            <input
              className={styles.modalInput}
              value={address}
              onChange={(e) => setAddress(e.target.value)}
              placeholder="fern:<pubkey>@<relay>,<relay>  or  https://bracken.example.com/?group=<pubkey>&relays="
              disabled={joinBusy}
              autoFocus
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleJoin()
              }}
            />
            {joinProgress.map((line, i) => (
              <div key={i} className={styles.progressLine}>
                <span className={styles.progressCheck}>✓</span>
                {line}
              </div>
            ))}
            {joinError && <p className={styles.errorText}>{joinError}</p>}
            <button
              className={styles.primaryBtn}
              onClick={handleJoin}
              disabled={!canJoin}
            >
              Join
            </button>
          </>
        ) : (
          <>
            <div className={styles.modalField}>
              <span className={styles.profileLabel}>Group name</span>
              <input
                className={styles.modalInput}
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="My Group"
                disabled={createBusy}
                autoFocus
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleCreate()
                }}
              />
            </div>

            <div className={styles.modalField}>
              <span className={styles.profileLabel}>Description (optional)</span>
              <textarea
                className={styles.modalTextarea}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="What is this group about?"
                disabled={createBusy}
                rows={2}
              />
            </div>

            <div className={styles.modalField}>
              <span className={styles.profileLabel}>Relay hints</span>
              <input
                className={styles.modalInput}
                value={relays}
                onChange={(e) => setRelays(e.target.value)}
                placeholder={`${DEFAULT_RELAY_HINTS[0]}, wss://relay.example.com`}
                disabled={createBusy}
                spellCheck={false}
              />
              <span className={styles.modalHint}>
                Comma or space separated. At least one canonical relay.
              </span>
            </div>

            <label className={styles.checkboxRow}>
              <input
                type="checkbox"
                checked={isPublic}
                onChange={(e) => setIsPublic(e.target.checked)}
                disabled={createBusy}
              />
              <span>Public group (anyone can join freely)</span>
            </label>

            {createProgress.map((line, i) => (
              <div key={i} className={styles.progressLine}>
                <span className={styles.progressCheck}>✓</span>
                {line}
              </div>
            ))}
            {createError && <p className={styles.errorText}>{createError}</p>}
            <button
              className={styles.primaryBtn}
              onClick={handleCreate}
              disabled={!canCreate}
            >
              Create
            </button>
          </>
        )}
      </div>
    </div>
  )
}
