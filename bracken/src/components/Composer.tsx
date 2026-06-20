import { useState, useRef, useEffect, useMemo } from 'react'
import styles from '../styles/components.module.css'

interface Props {
  channelName: string
  canPost: boolean
  disabledReason?: string
  onSend: (text: string, channel?: string) => Promise<boolean>
  onCommand?: (command: string, args: string) => Promise<void>
  commands: SlashCommand[]
}

export interface SlashCommand {
  cmd: string
  desc: string
}

export function Composer({
  channelName,
  canPost,
  disabledReason,
  onSend,
  onCommand,
  commands,
}: Props) {
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const isSlash = text.startsWith('/')

  const matchingCommands = useMemo(() => {
    if (!isSlash) return []
    const commandPart = text.split(/\s+/, 1)[0].toLowerCase()
    return commands.filter((c) => c.cmd.toLowerCase().startsWith(commandPart))
  }, [text, isSlash, commands])

  useEffect(() => {
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = `${Math.min(ta.scrollHeight, 120)}px`
  }, [text])

  const handleSend = async () => {
    const trimmed = text.trim()
    if (!trimmed || !canPost || sending) return

    if (isSlash && onCommand) {
      const spaceIdx = trimmed.indexOf(' ')
      const cmd = spaceIdx === -1 ? trimmed : trimmed.slice(0, spaceIdx)
      const args = spaceIdx === -1 ? '' : trimmed.slice(spaceIdx + 1)
      const cmdDef = commands.find((c) => c.cmd === cmd)
      if (cmdDef) {
        setSending(true)
        setText('')
        try {
          await onCommand(cmd, args)
        } finally {
          setSending(false)
        }
        return
      }
    }

    setSending(true)
    setText('')
    try {
      await onSend(trimmed, channelName)
    } finally {
      setSending(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  if (!canPost) {
    return (
      <div className={styles.composer}>
        <div className={styles.composerDisabled}>
          {disabledReason ?? 'You cannot post in this group.'}
        </div>
      </div>
    )
  }

  return (
    <div className={styles.composer}>
      {isSlash && matchingCommands.length > 0 && (
        <div className={styles.commandPopup}>
          {matchingCommands.map((c) => (
            <div
              key={c.cmd}
              className={styles.commandItem}
              onClick={() => {
                setText(c.cmd + ' ')
                textareaRef.current?.focus()
              }}
            >
              <span className={styles.commandName}>{c.cmd}</span>
              <span className={styles.commandDesc}>{c.desc}</span>
            </div>
          ))}
        </div>
      )}
      <div className={styles.composerInner}>
        <textarea
          ref={textareaRef}
          className={styles.composerTextarea}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={`Message #${channelName}`}
          rows={1}
        />
        <button
          className={styles.composerSendBtn}
          onClick={handleSend}
          disabled={!text.trim() || sending}
          title="Send"
        >
          ↑
        </button>
      </div>
    </div>
  )
}
