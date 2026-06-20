import { useEffect, useRef, useState, useMemo, useCallback } from 'react'
import type { FernEvent } from '../fern/events'
import { truncateId, relativeTime, absoluteTime } from '../fern/utils'
import { ProfilePopup } from './ProfilePopup'
import { Avatar } from './Avatar'
import styles from '../styles/components.module.css'

interface Props {
  events: FernEvent[]
  rejectedIds: Set<string>
  connectedEventIds: Set<string>
  localEventIds: Set<string>
  mods: Set<string>
  joined: Set<string>
  nicknames: Map<string, string>
  banned: Set<string>
  deliveries: Record<string, MessageDelivery>
  viewerPubkey?: string
  onModAction?: (type: string, targetPubkey: string, extra?: Record<string, unknown>) => Promise<void>
  onRetryMessage?: (eventId: string) => Promise<void>
}

interface DisplayRow {
  type: 'message' | 'gap' | 'system'
  event?: FernEvent
  gapId?: string
  ts: number
}

interface MessageDelivery {
  state: 'sending' | 'failed'
  ok: number
  total: number
  error?: string
}

const MOD_TYPES = new Set(['kick', 'ban', 'unban', 'invite', 'mod_add', 'mod_remove', 'join', 'leave', 'genesis', 'metadata_update', 'relay_update'])

function formatModAction(event: FernEvent): { clickable?: boolean; pubkey?: string; text: string }[] {
  const a: { clickable?: boolean; pubkey?: string; text: string }[] = []
  const author = { clickable: true, pubkey: event.author, text: truncateId(event.author) }
  const t = event.type
  const targetPubkey = (event.content['target'] as string) || ''
  const targetName = targetPubkey ? truncateId(targetPubkey) : '?'
  const target = { clickable: true, pubkey: targetPubkey, text: targetName }
  const inviteePubkey = (event.content['invitee'] as string) || ''
  const inviteeName = inviteePubkey ? truncateId(inviteePubkey) : '?'
  const invitee = { clickable: true, pubkey: inviteePubkey, text: inviteeName }
  if (t === 'kick') { a.push(author, { text: ' kicked ' }, target) }
  else if (t === 'ban') { a.push(author, { text: ' banned ' }, target); const r = event.content['reason'] as string; if (r) a.push({ text: ` (${r})` }) }
  else if (t === 'unban') { a.push(author, { text: ' unbanned ' }, target) }
  else if (t === 'invite') { a.push(author, { text: ' invited ' }, invitee) }
  else if (t === 'mod_add') { a.push(author, { text: ' promoted ' }, target, { text: ' to mod' }) }
  else if (t === 'mod_remove') { a.push(author, { text: ' demoted ' }, target) }
  else if (t === 'join') { a.push(author, { text: ' joined the group' }) }
  else if (t === 'leave') { a.push(author, { text: ' left the group' }) }
  else if (t === 'genesis') { a.push(author, { text: ' created the group' }) }
  else if (t === 'metadata_update') {
    a.push(author, { text: ' updated ' })
    const name = event.content['name'] as string | undefined
    const desc = event.content['description'] as string | undefined
    if (name !== undefined && desc !== undefined) a.push({ text: 'group name and description' })
    else if (name !== undefined) a.push({ text: `group name to '${name}'` })
    else if (desc !== undefined) a.push({ text: 'group description' })
    else a.push({ text: 'group info' })
  }
  else if (t === 'relay_update') {
    const relays = (event.content['relays'] as unknown[]) || []
    const count = relays.filter((r): r is string => typeof r === 'string').length
    a.push(author, { text: ` updated relays (${count} relay${count !== 1 ? 's' : ''})` })
  }
  return a
}

export function MessageList({
  events,
  rejectedIds,
  connectedEventIds,
  localEventIds,
  mods,
  joined,
  nicknames,
  banned,
  deliveries,
  viewerPubkey = '',
  onModAction,
  onRetryMessage,
}: Props) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const [atBottom, setAtBottom] = useState(true)
  const [profile, setProfile] = useState<{
    pubkey: string
  } | null>(null)

  const rows = useMemo(() => {
    const relevantEvents = events
      .filter((e) => connectedEventIds.has(e.id))
      .filter((e) => e.type === 'chat.message' || MOD_TYPES.has(e.type))
      .sort((a, b) => {
        if (a.ts !== b.ts) return a.ts - b.ts
        return a.id < b.id ? -1 : a.id > b.id ? 1 : 0
      })

    const displayRows: DisplayRow[] = []
    const seenGapIds = new Set<string>()

    for (const event of relevantEvents) {
      for (const parentId of event.parents) {
        if (!localEventIds.has(parentId) && !seenGapIds.has(parentId)) {
          seenGapIds.add(parentId)
          displayRows.push({
            type: 'gap',
            gapId: parentId,
            ts: event.ts - 1,
          })
        }
      }
      if (MOD_TYPES.has(event.type)) {
        displayRows.push({ type: 'system', event, ts: event.ts })
      } else {
        displayRows.push({ type: 'message', event, ts: event.ts })
      }
    }

    return displayRows
  }, [events, connectedEventIds, localEventIds])

  useEffect(() => {
    if (atBottom && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [rows, atBottom])

  const handleScroll = () => {
    if (!scrollRef.current) return
    const el = scrollRef.current
    const isAtBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40
    setAtBottom(isAtBottom)
  }

  const scrollToBottom = () => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
      setAtBottom(true)
    }
  }

  const openProfile = useCallback((pubkey: string) => {
    setProfile({ pubkey })
  }, [])

  let lastAuthor: string | null = null
  let lastTs = 0

  return (
    <div className={styles.messageList} ref={scrollRef} onScroll={handleScroll}>
      {rows.length === 0 && (
        <div className={styles.emptyState}>
          <p className={styles.emptyStateTitle}>No messages yet</p>
        </div>
      )}
      {rows.map((row) => {
        if (row.type === 'gap') {
          lastAuthor = null
          return (
            <div key={`gap-${row.gapId}`} className={styles.gapIndicator}>
              <div className={styles.gapLine} />
              <div
                className={styles.gapPill}
                onClick={() => navigator.clipboard.writeText(row.gapId!)}
                title="Click to copy full event ID"
              >
                <span>⋯</span>
                <span>missing event</span>
                <span className="mono">{truncateId(row.gapId!)}</span>
              </div>
              <div className={styles.gapLine} />
            </div>
          )
        }

        if (row.type === 'system') {
          lastAuthor = null
          const parts = formatModAction(row.event!)
          return (
            <div key={`sys-${row.event!.id}`} className={styles.systemRow}>
              <span className={styles.systemText}>
                {parts.map((part, i) =>
                  part.clickable && part.pubkey ? (
                    <span
                      key={i}
                      className={styles.systemChip + (mods.has(part.pubkey) ? ' ' + styles.systemChipMod : '')}
                      onClick={() => openProfile(part.pubkey!)}
                      title="Click to view profile"
                    >
                      {nicknames.get(part.pubkey) ?? truncateId(part.pubkey)}
                      {part.pubkey === viewerPubkey && ' (You)'}
                    </span>
                  ) : (
                    <span key={i}>{part.text}</span>
                  ),
                )}
              </span>
            </div>
          )
        }

        const event = row.event!
        const isRejected = rejectedIds.has(event.id)
        const collapsed = lastAuthor === event.author && event.ts - lastTs < 300
        lastAuthor = event.author
        lastTs = event.ts

        const isMod = mods.has(event.author)
        const nick = nicknames.get(event.author)
        const delivery = deliveries[event.id]

        return (
          <div
            key={event.id}
            className={`${styles.messageRow} ${
              collapsed ? styles.messageRowCollapsed : ''
            }`}
          >
            {!collapsed && (
              <div className={styles.messageAuthor}>
                <span
                  className={styles.messageAvatar}
                  onClick={() => openProfile(event.author)}
                  title="Click to view profile"
                >
                  <Avatar value={event.author} size={28} />
                </span>
                <span
                  className={`${styles.authorChip} ${isMod ? styles.authorChipMod : ''}`}
                  onClick={() => openProfile(event.author)}
                  title="Click to view profile"
                >
                  {nick ?? truncateId(event.author)}
                  {event.author === viewerPubkey && ' (You)'}
                </span>
                <span className={styles.timestamp} title={absoluteTime(event.ts)}>
                  {relativeTime(event.ts)}
                </span>
              </div>
            )}
            <div
              className={`${styles.messageBody} ${
                isRejected ? styles.messageRejected : ''
              }`}
            >
              {event.content['text'] as string}
              {isRejected && (
                <span className={styles.rejectedTag}>[not authorized]</span>
              )}
            </div>
            {delivery && (
              <div
                className={`${styles.deliveryStatus} ${
                  delivery.state === 'failed' ? styles.deliveryStatusFailed : ''
                }`}
              >
                {delivery.state === 'sending' ? (
                  <span>Sending...</span>
                ) : (
                  <>
                    <span>
                      Failed to send
                      {delivery.total > 0 ? ` (${delivery.ok}/${delivery.total} relays)` : ''}
                    </span>
                    {onRetryMessage && (
                      <button
                        className={styles.deliveryRetryBtn}
                        onClick={() => onRetryMessage(event.id)}
                      >
                        Retry
                      </button>
                    )}
                  </>
                )}
              </div>
            )}
          </div>
        )
      })}
      {!atBottom && (
        <button className={styles.scrollBtn} onClick={scrollToBottom}>↓</button>
      )}
      {profile && (
        <ProfilePopup
          pubkey={profile.pubkey}
          nickname={nicknames.get(profile.pubkey) ?? null}
          isMod={mods.has(profile.pubkey)}
          isBanned={banned.has(profile.pubkey)}
          isMember={joined.has(profile.pubkey)}
          viewerIsMod={mods.has(viewerPubkey)}
          viewerPubkey={viewerPubkey}
          onClose={() => setProfile(null)}
          onModAction={onModAction}
        />
      )}
    </div>
  )
}
