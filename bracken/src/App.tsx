import { Suspense, lazy, useState, useMemo, useEffect, useCallback } from 'react'
import { useBracken } from './hooks/useBracken'
import { IdentitySetup } from './components/IdentitySetup'
import { InvitePreview, type PendingJoin } from './components/InvitePreview'
import { Sidebar } from './components/Sidebar'
import { MessageList } from './components/MessageList'
import type { SlashCommand } from './components/Composer'
import { Composer } from './components/Composer'
import { AddGroupModal } from './components/AddGroupModal'
import { MemberDrawer, RelayDrawer } from './components/Drawers'
import { FernLogo } from './components/FernLogo'
import { SettingsModal } from './components/SettingsModal'
import { GroupInfoModal } from './components/GroupInfoModal'
import { getEventIds } from './fern/db'
import { deriveGroupState } from './fern/state'
import type { FernEvent } from './fern/events'
import { isValidPubkey } from './fern/utils'
import { randomHexId } from './fern/utils'
import { useDefiniteOverlayClick } from './hooks/useDefiniteOverlayClick'
import styles from './styles/components.module.css'

const DagViewer = lazy(() =>
  import('./components/DagViewer').then((module) => ({ default: module.DagViewer })),
)

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

function computeChannelNames(events: FernEvent[]): Map<string, string> {
  const sorted = [...events]
    .filter((e) => e.type === 'genesis' || e.type === 'chat.channel_create' || e.type === 'chat.channel_update')
    .sort((a, b) => {
      if (a.type === 'genesis' && b.type !== 'genesis') return -1
      if (a.type !== 'genesis' && b.type === 'genesis') return 1
      if (a.ts !== b.ts) return a.ts - b.ts
      return a.id < b.id ? -1 : a.id > b.id ? 1 : 0
    })
  const names = new Map<string, string>()
  for (const event of sorted) {
    if (event.type === 'genesis') {
      const raw = event.content['chat.channels']
      if (!Array.isArray(raw)) continue
      for (const entry of raw) {
        if (typeof entry === 'object' && entry !== null && !Array.isArray(entry)) {
          const record = entry as Record<string, unknown>
          const id = String(record['id'] ?? '').trim()
          const name = String(record['name'] ?? id).trim()
          if (id && name) names.set(id, name)
        }
      }
    } else if (event.type === 'chat.channel_create') {
      const id = event.content['id'] as string | undefined
      const name = event.content['name'] as string | undefined
      if (id && name) names.set(id, name)
    } else if (event.type === 'chat.channel_update') {
      const id = event.content['id'] as string | undefined
      const name = event.content['name'] as string | undefined
      if (id && name) names.set(id, name)
    }
  }
  return names
}

const USER_COMMANDS: SlashCommand[] = [
  { cmd: '/nickname', desc: 'Set your display name' },
]

const ADMIN_COMMANDS: SlashCommand[] = [
  { cmd: '/kick', desc: 'Kick a member by pubkey' },
  { cmd: '/ban', desc: 'Ban a member by pubkey' },
  { cmd: '/unban', desc: 'Lift a ban by pubkey' },
  { cmd: '/invite', desc: 'Invite a pubkey' },
  { cmd: '/promote', desc: 'Promote a member to admin' },
  { cmd: '/demote', desc: 'Demote an admin' },
  { cmd: '/relay-add', desc: 'Add canonical relays' },
  { cmd: '/relay-remove', desc: 'Remove canonical relays' },
  { cmd: '/name', desc: 'Set group name' },
  { cmd: '/description', desc: 'Set group description' },
  { cmd: '/channel-create', desc: 'Create a new channel' },
  { cmd: '/channel-delete', desc: 'Delete a channel' },
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

function pendingJoinFromLocation(): PendingJoin | null {
  const params = new URLSearchParams(window.location.search)
  const group = params.get('group')?.trim()
  if (!group || !isValidPubkey(group)) return null
  const relaysParam = params.get('relays') ?? ''
  const relays = relaysParam
    .split(/[\s,]+/)
    .map((r) => r.trim())
    .filter(Boolean)
  return { pubkey: group, relays }
}

function dagGroupFromLocation(): string | null {
  const match = window.location.pathname.match(/^\/dag\/([0-9a-f]{64})$/)
  return match && isValidPubkey(match[1]) ? match[1] : null
}

function dagPath(groupPubkey: string): string {
  return `/dag/${groupPubkey}`
}

export default function App() {
  const bracken = useBracken()
  const [showAddGroup, setShowAddGroup] = useState(false)
  const [showMembers, setShowMembers] = useState(false)
  const [showRelays, setShowRelays] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [showGroupInfo, setShowGroupInfo] = useState(false)
  const [showHelp, setShowHelp] = useState(false)
  const [dagGroupPubkey, setDagGroupPubkey] = useState<string | null>(() => dagGroupFromLocation())
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [localEventIds, setLocalEventIds] = useState<Set<string>>(new Set())
  const [pendingJoin, setPendingJoin] = useState<PendingJoin | null>(() => pendingJoinFromLocation())
  const [modalInitial, setModalInitial] = useState<{ address?: string; error?: string | null } | null>(null)
  const helpOverlayHandlers = useDefiniteOverlayClick(() => setShowHelp(false))

  const openAddGroup = useCallback((initial?: { address?: string; error?: string | null }) => {
    setModalInitial(initial ?? null)
    setShowAddGroup(true)
  }, [])

  const closeAddGroup = useCallback(() => {
    setShowAddGroup(false)
    setModalInitial(null)
  }, [])

  const cancelInvite = useCallback(() => {
    setPendingJoin(null)
  }, [])

  const openDag = useCallback((groupPubkey: string) => {
    window.history.pushState(null, '', dagPath(groupPubkey))
    setDagGroupPubkey(groupPubkey)
  }, [])

  const closeDag = useCallback(() => {
    window.history.pushState(null, '', '/')
    setDagGroupPubkey(null)
  }, [])

  useEffect(() => {
    getEventIds().then(setLocalEventIds)
  }, [bracken.events])

  useEffect(() => {
    const syncDagRoute = () => setDagGroupPubkey(dagGroupFromLocation())
    window.addEventListener('popstate', syncDagRoute)
    return () => window.removeEventListener('popstate', syncDagRoute)
  }, [])

  useEffect(() => {
    if (!pendingJoin) return
    window.history.replaceState(null, '', window.location.pathname)
  }, [pendingJoin])

  const isAlreadyMember =
    pendingJoin !== null &&
    bracken.groups.some((g) => g.pubkey === pendingJoin.pubkey)
  const dagGroupEntry = dagGroupPubkey
    ? bracken.groups.find((g) => g.pubkey === dagGroupPubkey) ?? null
    : null

  useEffect(() => {
    if (!dagGroupPubkey || !dagGroupEntry || bracken.activeGroup === dagGroupPubkey) return
    bracken.setActiveGroup(dagGroupPubkey)
  }, [bracken, dagGroupEntry, dagGroupPubkey])

  const rejectedIds = useMemo(() => {
    if (!bracken.events || bracken.events.length === 0) return new Set<string>()
    const { rejected } = deriveGroupState(bracken.events)
    return new Set(rejected.map((e) => e.id))
  }, [bracken.events])

  const acceptedEventIds = useMemo(() => {
    if (!bracken.events || bracken.events.length === 0) return new Set<string>()
    return deriveGroupState(bracken.events).acceptedIds
  }, [bracken.events])

  const nicknames = useMemo(() => {
    if (!bracken.events) return new Map<string, string>()
    return computeNicknames(bracken.events.filter((event) => acceptedEventIds.has(event.id)))
  }, [bracken.events, acceptedEventIds])

  const channelNames = useMemo(() => {
    return computeChannelNames(bracken.events.filter((event) => acceptedEventIds.has(event.id)))
  }, [bracken.events, acceptedEventIds])

  const admins = useMemo(() => {
    return bracken.state?.admins ?? new Set<string>()
  }, [bracken.state])

  const [selectedChannels, setSelectedChannels] = useState<Record<string, string>>({})
  const storedSelectedChannel = bracken.activeGroup
    ? selectedChannels[bracken.activeGroup] ?? bracken.state?.chatSettings.default_channel ?? ''
    : ''

  const channels = useMemo(() => {
    if (!bracken.state) return [] as { id: string; name: string; description: string; number: number }[]
    return [...bracken.state.channels.values()].sort((a, b) => a.position - b.position || a.name.localeCompare(b.name))
  }, [bracken.state])
  const selectedChannel = bracken.state?.channels.has(storedSelectedChannel)
    ? storedSelectedChannel
    : bracken.state?.channels.has(bracken.state.chatSettings.default_channel)
      ? bracken.state.chatSettings.default_channel
      : channels[0]?.id ?? ''

  const isViewerAdmin = bracken.identity ? admins.has(bracken.identity.publicKey) : false
  const slashCommands = useMemo(() => {
    return isViewerAdmin ? [...USER_COMMANDS, ...ADMIN_COMMANDS] : USER_COMMANDS
  }, [isViewerAdmin])

  if (bracken.loading) {
    return (
      <div className={styles.emptyState}>
        <FernLogo size={28} />
      </div>
    )
  }

  if (dagGroupPubkey && !dagGroupEntry) {
    return (
      <div className={styles.emptyState}>
        <p className={styles.emptyStateTitle}>You're not in that group</p>
        <button className={styles.secondaryBtn} onClick={closeDag}>Back to chat</button>
      </div>
    )
  }

  if (pendingJoin) {
    return (
      <InvitePreview
        pendingJoin={pendingJoin}
        hasIdentity={bracken.identity !== null}
        alreadyMember={isAlreadyMember}
        onImportIdentity={bracken.importIdentity}
        onJoin={bracken.joinGroup}
        onSwitchToGroup={bracken.setActiveGroup}
        onCancel={cancelInvite}
      />
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

  if (dagGroupPubkey && dagGroupEntry && activeGroupEntry?.pubkey !== dagGroupPubkey) {
    return (
      <div className={styles.emptyState}>
        <FernLogo size={28} />
      </div>
    )
  }

  if (dagGroupPubkey && dagGroupEntry && activeGroupEntry?.pubkey === dagGroupPubkey) {
    return (
      <Suspense fallback={<div className={styles.emptyState}><FernLogo size={28} /></div>}>
        <DagViewer
          groupName={bracken.state?.metadata.name || dagGroupEntry.name}
          groupPubkey={dagGroupEntry.pubkey}
          events={bracken.events}
          onClose={closeDag}
        />
      </Suspense>
    )
  }

  const totalRelays = bracken.relayConns.length
    || (bracken.state?.relays.length ?? activeGroupEntry?.relays.length ?? 0)
  const connectedRelays = bracken.relayConns.filter((c) => c.connected).length
  const relayCountClass =
    connectedRelays >= 3
      ? styles.relayCountGreen
      : connectedRelays === 2
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
          channels={channels}
          selectedChannel={selectedChannel}
          onSelectGroup={(pk) => {
            bracken.setActiveGroup(pk)
            setSidebarOpen(false)
          }}
          onSelectChannel={(channelId, groupPubkey) => {
            setSelectedChannels((prev) => ({ ...prev, [groupPubkey]: channelId }))
            setSidebarOpen(false)
          }}
          onGroupInfoClick={(pk) => {
            bracken.setActiveGroup(pk)
            setShowGroupInfo(true)
            setSidebarOpen(false)
          }}
          onAddGroupClick={() => {
            openAddGroup()
            setSidebarOpen(false)
          }}
          onIdentityClick={() => {
            setShowSettings(true)
            setSidebarOpen(false)
          }}
          onHelpClick={() => setShowHelp(true)}
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
              <span className={styles.channelName}>
                # {bracken.state?.channels.get(selectedChannel)?.name ?? selectedChannel}
              </span>
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
                  title={`${connectedRelays} of ${totalRelays} canonical relay${totalRelays === 1 ? '' : 's'} connected`}
                >
                  {connectedRelays}/{totalRelays}
                </button>
              </div>
            </div>

            <MessageList
              events={bracken.events}
              rejectedIds={rejectedIds}
              connectedEventIds={acceptedEventIds}
              localEventIds={localEventIds}
              admins={admins}
              joined={joinedSet}
              nicknames={nicknames}
              banned={bannedSet}
              deliveries={bracken.messageDeliveries}
              channelNames={channelNames}
              viewerPubkey={userPubkey}
              selectedChannel={selectedChannel}
              onAdminAction={bracken.adminAction}
              onRetryMessage={bracken.retryMessage}
            />

            <Composer
              channelId={selectedChannel}
              channelName={bracken.state?.channels.get(selectedChannel)?.name ?? selectedChannel}
              canPost={canPost && !isBanned}
              disabledReason={
                isBanned
                  ? 'You are banned from this group.'
                  : !canPost
                    ? 'You have not joined this group. Ask an admin to invite you, or join if public.'
                    : undefined
              }
              onSend={bracken.sendMessage}
              onCommand={async (cmd, args) => {
                if (cmd === '/nickname' && args) {
                  await bracken.setNickname(args)
                } else if (isViewerAdmin && cmd === '/kick') {
                  await bracken.adminAction('kick', firstArg(args))
                } else if (isViewerAdmin && cmd === '/ban') {
                  const target = firstArg(args)
                  const reason = args.trim().slice(target.length).trim()
                  await bracken.adminAction('ban', target, { reason, until: null })
                } else if (isViewerAdmin && cmd === '/unban') {
                  await bracken.adminAction('unban', firstArg(args))
                } else if (isViewerAdmin && cmd === '/invite') {
                  await bracken.adminAction('invite', firstArg(args))
                } else if (isViewerAdmin && cmd === '/promote') {
                  await bracken.adminAction('admin_add', firstArg(args))
                } else if (isViewerAdmin && cmd === '/demote') {
                  await bracken.adminAction('admin_remove', firstArg(args))
                } else if (isViewerAdmin && cmd === '/relay-add') {
                  const relaysToAdd = parseRelayArgs(args)
                  if (relaysToAdd.length > 0) {
                    const currentRelays = bracken.state?.relays ?? activeGroupEntry.relays
                    await bracken.adminAction('relay_update', '', {
                      relays: uniqueRelays([...currentRelays, ...relaysToAdd]),
                    })
                  }
                } else if (isViewerAdmin && cmd === '/relay-remove') {
                  const relaysToRemove = new Set(parseRelayArgs(args))
                  if (relaysToRemove.size > 0) {
                    const currentRelays = bracken.state?.relays ?? activeGroupEntry.relays
                    const relays = currentRelays.filter((relay) => !relaysToRemove.has(relay))
                    if (relays.length > 0) {
                      await bracken.adminAction('relay_update', '', { relays })
                    }
                  }
                } else if (isViewerAdmin && cmd === '/name' && args.trim()) {
                  await bracken.adminAction('metadata_update', '', { name: args.trim() })
                } else if (isViewerAdmin && cmd === '/description') {
                  await bracken.adminAction('metadata_update', '', { description: args.trim() })
                } else if (isViewerAdmin && cmd === '/channel-create' && args.trim()) {
                  await bracken.adminAction('chat.channel_create', '', { id: randomHexId(), name: args.trim() })
                } else if (isViewerAdmin && cmd === '/channel-delete' && args.trim()) {
                  const channel = [...(bracken.state?.channels.values() ?? [])].find((ch) => ch.name === args.trim() || ch.id === args.trim())
                  if (channel) await bracken.adminAction('chat.channel_delete', '', { id: channel.id, name: channel.name })
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
              onClick={() => openAddGroup()}
            >
              Add a group
            </button>
          </div>
        )}
      </div>

      {showAddGroup && (
        <AddGroupModal
          onJoin={bracken.joinGroup}
          onCreate={bracken.createGroup}
          onClose={closeAddGroup}
          initialAddress={modalInitial?.address}
          initialError={modalInitial?.error ?? null}
        />
      )}
      {showMembers && bracken.state && (
        <MemberDrawer
          state={bracken.state}
          nicknames={nicknames}
          viewerPubkey={userPubkey}
          onClose={() => setShowMembers(false)}
          onAdminAction={bracken.adminAction}
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
          onViewDag={() => {
            setShowGroupInfo(false)
            openDag(activeGroupEntry.pubkey)
          }}
          onLeaveGroup={async () => {
            await bracken.leaveGroup(activeGroupEntry.pubkey)
            setShowGroupInfo(false)
          }}
          onClose={() => setShowGroupInfo(false)}
        />
      )}
      {showSettings && (
        <SettingsModal
          pubkey={bracken.identity.publicKey}
          privateKey={bracken.identity.seed}
          currentNickname={bracken.defaultNickname ?? nicknames.get(bracken.identity.publicKey) ?? null}
          onClose={() => setShowSettings(false)}
          onSetNickname={bracken.setNickname}
          onLogout={bracken.logout}
        />
      )}
      {showHelp && (
        <div className={styles.modalOverlay} {...helpOverlayHandlers}>
          <div className={styles.helpModal}>
            <div className={styles.groupInfoHeader}>
              <div>
                <div className={styles.groupInfoTitle}>Bracken</div>
                <div className={styles.groupInfoSubtitle}>Alpha version</div>
              </div>
              <button className={styles.drawerClose} onClick={() => setShowHelp(false)}>✕</button>
            </div>
            <p>
              Bracken is a group messaging app built on the FERN protocol. It
              supports decentralized, censorship-resistant communication by
              syncing signed group events across many relay servers.
            </p>
            <p>
              Everything runs client-side: identity keys, message verification,
              group state, and event validation happen in your browser.
            </p>
            <a
              className={styles.helpLink}
              href="https://github.com/hurndev/FERN"
              target="_blank"
              rel="noreferrer"
            >
              GitHub
            </a>
          </div>
        </div>
      )}
    </div>
  )
}
