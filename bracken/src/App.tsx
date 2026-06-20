import { useState, useMemo, useEffect } from 'react'
import { useBracken } from './hooks/useBracken'
import { IdentitySetup } from './components/IdentitySetup'
import { Sidebar } from './components/Sidebar'
import { MessageList } from './components/MessageList'
import type { SlashCommand } from './components/Composer'
import { Composer } from './components/Composer'
import { JoinModal } from './components/JoinModal'
import { MemberDrawer, RelayDrawer } from './components/Drawers'
import { FernLogo } from './components/FernLogo'
import { SettingsModal } from './components/SettingsModal'
import { GroupInfoModal } from './components/GroupInfoModal'
import { getEventIds } from './fern/db'
import { deriveGroupState } from './fern/state'
import { computeConnectedEventIds } from './fern/dag'
import type { FernEvent } from './fern/events'
import styles from './styles/components.module.css'

function computeNicknames(events: FernEvent[]): Map<string, string> {
  const sorted = [...events]
    .filter((e) => e.type === 'chat.nickname_set')
    .sort((a, b) => {
      if (a.ts !== b.ts) return a.ts - b.ts
      return a.id < b.id ? -1 : a.id > b.id ? 1 : 0
    })
  const nicknames = new Map<string, string>()
  for (const e of sorted) {
    const nick = e.content['nickname'] as string
    if (!nick) continue
    nicknames.set(e.author, nick)
  }
  return nicknames
}

const USER_COMMANDS: SlashCommand[] = [
  { cmd: '/nickname', desc: 'Set your display name' },
]

const MOD_COMMANDS: SlashCommand[] = [
  { cmd: '/kick', desc: 'Kick a member by pubkey' },
  { cmd: '/ban', desc: 'Ban a member by pubkey' },
  { cmd: '/unban', desc: 'Lift a ban by pubkey' },
  { cmd: '/invite', desc: 'Invite a pubkey' },
  { cmd: '/promote', desc: 'Promote a member to mod' },
  { cmd: '/demote', desc: 'Demote a mod' },
  { cmd: '/relay-add', desc: 'Add canonical relays' },
  { cmd: '/relay-remove', desc: 'Remove canonical relays' },
  { cmd: '/name', desc: 'Set group name' },
  { cmd: '/description', desc: 'Set group description' },
]

function firstArg(args: string): string {
  return args.trim().split(/\s+/, 1)[0] ?? ''
}

function parseRelayArgs(args: string): string[] {
  return args
    .split(/[\s,]+/)
    .map((relay) => relay.trim())
    .filter(Boolean)
}

function uniqueRelays(relays: string[]): string[] {
  return [...new Set(relays)]
}

export default function App() {
  const bracken = useBracken()
  const [showJoin, setShowJoin] = useState(false)
  const [showMembers, setShowMembers] = useState(false)
  const [showRelays, setShowRelays] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [showGroupInfo, setShowGroupInfo] = useState(false)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [localEventIds, setLocalEventIds] = useState<Set<string>>(new Set())

  useEffect(() => {
    getEventIds().then(setLocalEventIds)
  }, [bracken.events])

  const rejectedIds = useMemo(() => {
    if (!bracken.events || bracken.events.length === 0) return new Set<string>()
    const { rejected } = deriveGroupState(bracken.events)
    return new Set(rejected.map((e) => e.id))
  }, [bracken.events])

  const connectedEventIds = useMemo(() => {
    return computeConnectedEventIds(bracken.events)
  }, [bracken.events])

  const nicknames = useMemo(() => {
    if (!bracken.events) return new Map<string, string>()
    return computeNicknames(bracken.events.filter((event) => connectedEventIds.has(event.id)))
  }, [bracken.events, connectedEventIds])

  const mods = useMemo(() => {
    return bracken.state?.mods ?? new Set<string>()
  }, [bracken.state])

  const isViewerMod = bracken.identity ? mods.has(bracken.identity.publicKey) : false
  const slashCommands = useMemo(() => {
    return isViewerMod ? [...USER_COMMANDS, ...MOD_COMMANDS] : USER_COMMANDS
  }, [isViewerMod])

  if (bracken.loading) {
    return (
      <div className={styles.emptyState}>
        <FernLogo size={28} />
      </div>
    )
  }

  if (!bracken.identity) {
    return (
      <IdentitySetup
        onImport={bracken.importIdentity}
      />
    )
  }

  const userPubkey = bracken.identity.publicKey
  const bannedSet = new Set(bracken.state?.banned.keys() ?? [])
  const joinedSet = bracken.state?.joined ?? new Set<string>()

  const activeGroupEntry = bracken.groups.find(
    (g) => g.pubkey === bracken.activeGroup,
  )
  const relayCount = bracken.state?.relays.length ?? activeGroupEntry?.relays.length ?? 0
  const relayCountClass =
    relayCount >= 3
      ? styles.relayCountGreen
      : relayCount === 2
        ? styles.relayCountAmber
        : styles.relayCountRed
  const canPost =
    bracken.state?.joined.has(bracken.identity.publicKey) ?? false
  const isBanned = bracken.state
    ? bracken.state.banned.has(bracken.identity.publicKey)
    : false

  return (
    <div className={styles.appShell}>
      {sidebarOpen && (
        <div className={styles.sidebarScrim} onClick={() => setSidebarOpen(false)} />
      )}
      <div className={`${styles.sidebarWrap} ${sidebarOpen ? styles.sidebarWrapOpen : ''}`}>
        <Sidebar
          groups={bracken.groups}
          activeGroup={bracken.activeGroup}
          identityPubkey={bracken.identity.publicKey}
          relayConns={bracken.relayConns}
          onSelectGroup={(pk) => {
            bracken.setActiveGroup(pk)
            setSidebarOpen(false)
          }}
          onJoinClick={() => {
            setShowJoin(true)
            setSidebarOpen(false)
          }}
          onIdentityClick={() => {
            setShowSettings(true)
            setSidebarOpen(false)
          }}
        />
      </div>

      <div className={styles.mainArea}>
        {activeGroupEntry ? (
          <>
            <div className={styles.channelHeader}>
              <button
                className={styles.hamburger}
                onClick={() => setSidebarOpen(true)}
                title="Menu"
              >
                ☰
              </button>
              <span className={styles.channelName}># general</span>
              <button
                className={styles.groupLabelBtn}
                onClick={() => setShowGroupInfo(true)}
              >
                {bracken.state?.metadata.name || activeGroupEntry.name}
              </button>
              <div className={styles.headerRight}>
                <button
                  className={styles.memberBtn}
                  onClick={() => setShowMembers(true)}
                >
                  {bracken.state?.joined.size ?? 0} members
                </button>
                <button
                  className={`${styles.relayCountBadge} ${relayCountClass}`}
                  onClick={() => setShowRelays(true)}
                  title={`${relayCount} canonical relay${relayCount === 1 ? '' : 's'}`}
                >
                  {relayCount}
                </button>
              </div>
            </div>

            <MessageList
              events={bracken.events}
              rejectedIds={rejectedIds}
              connectedEventIds={connectedEventIds}
              localEventIds={localEventIds}
              mods={mods}
              joined={joinedSet}
              nicknames={nicknames}
              banned={bannedSet}
              deliveries={bracken.messageDeliveries}
              viewerPubkey={userPubkey}
              onModAction={bracken.modAction}
              onRetryMessage={bracken.retryMessage}
            />

            <Composer
              channelName="general"
              canPost={canPost && !isBanned}
              disabledReason={
                isBanned
                  ? 'You are banned from this group.'
                  : !canPost
                    ? 'You have not joined this group. Ask a mod to invite you, or join if public.'
                    : undefined
              }
              onSend={bracken.sendMessage}
              onCommand={async (cmd, args) => {
                if (cmd === '/nickname' && args) {
                  await bracken.setNickname(args)
                } else if (isViewerMod && cmd === '/kick') {
                  await bracken.modAction('kick', firstArg(args))
                } else if (isViewerMod && cmd === '/ban') {
                  const target = firstArg(args)
                  const reason = args.trim().slice(target.length).trim()
                  await bracken.modAction('ban', target, { reason, until: null })
                } else if (isViewerMod && cmd === '/unban') {
                  await bracken.modAction('unban', firstArg(args))
                } else if (isViewerMod && cmd === '/invite') {
                  await bracken.modAction('invite', firstArg(args))
                } else if (isViewerMod && cmd === '/promote') {
                  await bracken.modAction('mod_add', firstArg(args))
                } else if (isViewerMod && cmd === '/demote') {
                  await bracken.modAction('mod_remove', firstArg(args))
                } else if (isViewerMod && cmd === '/relay-add') {
                  const relaysToAdd = parseRelayArgs(args)
                  if (relaysToAdd.length > 0) {
                    const currentRelays = bracken.state?.relays ?? activeGroupEntry.relays
                    await bracken.modAction('relay_update', '', {
                      relays: uniqueRelays([...currentRelays, ...relaysToAdd]),
                    })
                  }
                } else if (isViewerMod && cmd === '/relay-remove') {
                  const relaysToRemove = new Set(parseRelayArgs(args))
                  if (relaysToRemove.size > 0) {
                    const currentRelays = bracken.state?.relays ?? activeGroupEntry.relays
                    const relays = currentRelays.filter((relay) => !relaysToRemove.has(relay))
                    if (relays.length > 0) {
                      await bracken.modAction('relay_update', '', { relays })
                    }
                  }
                } else if (isViewerMod && cmd === '/name' && args.trim()) {
                  await bracken.modAction('metadata_update', '', { name: args.trim() })
                } else if (isViewerMod && cmd === '/description') {
                  await bracken.modAction('metadata_update', '', { description: args.trim() })
                }
              }}
              commands={slashCommands}
            />
          </>
        ) : (
          <div className={styles.emptyState}>
            <FernLogo size={32} />
            <p className={styles.emptyStateTitle}>No groups yet</p>
            <button
              className={styles.primaryBtn}
              onClick={() => setShowJoin(true)}
            >
              Join a group
            </button>
          </div>
        )}
      </div>

      {showJoin && (
        <JoinModal
          onJoin={bracken.joinGroup}
          onClose={() => setShowJoin(false)}
        />
      )}
      {showMembers && bracken.state && (
        <MemberDrawer
          state={bracken.state}
          nicknames={nicknames}
          viewerPubkey={userPubkey}
          onClose={() => setShowMembers(false)}
          onModAction={bracken.modAction}
        />
      )}
      {showRelays && (
        <RelayDrawer
          relayConns={bracken.relayConns}
          onClose={() => setShowRelays(false)}
        />
      )}
      {showGroupInfo && activeGroupEntry && (
        <GroupInfoModal
          name={bracken.state?.metadata.name || activeGroupEntry.name}
          pubkey={activeGroupEntry.pubkey}
          description={bracken.state?.metadata.description ?? ''}
          relays={bracken.state?.relays ?? activeGroupEntry.relays}
          onClose={() => setShowGroupInfo(false)}
        />
      )}
      {showSettings && (
        <SettingsModal
          pubkey={bracken.identity.publicKey}
          privateKey={bracken.identity.seed}
          currentNickname={nicknames.get(bracken.identity.publicKey) ?? null}
          onClose={() => setShowSettings(false)}
          onSetNickname={bracken.setNickname}
          onLogout={bracken.logout}
        />
      )}
    </div>
  )
}
