import { Suspense, lazy, useState, useMemo, useEffect, useCallback } from 'react'
import { useBracken, type PublishResult } from './hooks/useBracken'
import { IdentitySetup } from './components/IdentitySetup'
import { InvitePreview, type PendingJoin } from './components/InvitePreview'
import { Sidebar } from './components/Sidebar'
import { MessageList } from './components/MessageList'
import type { SlashCommand } from './components/Composer'
import { Composer } from './components/Composer'
import { AddGroupModal } from './components/AddGroupModal'
import { MemberDrawer, ValidatorDrawer } from './components/Drawers'
import { FernLogo } from './components/FernLogo'
import { SettingsModal } from './components/SettingsModal'
import { GroupInfoModal } from './components/GroupInfoModal'
import { deriveGroupState, type Channel } from './fern/state'
import { validatorQuorum as quorumFor } from './fern/bft'
import type { FernEvent } from './fern/events'
import { isValidPubkey } from './fern/utils'
import { randomHexId } from './fern/utils'
import { useDefiniteOverlayClick } from './hooks/useDefiniteOverlayClick'
import styles from './styles/components.module.css'

const ChainViewer = lazy(() =>
  import('./components/ChainViewer').then((module) => ({ default: module.ChainViewer })),
)

function compareEventOrder(a: FernEvent, b: FernEvent): number {
  if (a.type === 'genesis') return -1
  if (b.type === 'genesis') return 1
  if (a.bft?.status !== b.bft?.status) return a.bft?.status === 'finalized' ? -1 : 1
  return (a.bft?.height ?? Number.MAX_SAFE_INTEGER) - (b.bft?.height ?? Number.MAX_SAFE_INTEGER)
    || (a.bft?.position ?? Number.MAX_SAFE_INTEGER) - (b.bft?.position ?? Number.MAX_SAFE_INTEGER)
    || a.ts - b.ts
    || a.id.localeCompare(b.id)
}

function computeNicknames(events: FernEvent[]): Map<string, string> {
  const sorted = [...events]
    .filter((e) => e.type === 'chat.nickname_set')
    .sort(compareEventOrder)
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
    .sort(compareEventOrder)
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

const MOD_COMMANDS: SlashCommand[] = [
  { cmd: '/kick', desc: 'Kick a member by pubkey' },
  { cmd: '/ban', desc: 'Ban a member by pubkey' },
  { cmd: '/unban', desc: 'Lift a ban by pubkey' },
  { cmd: '/invite', desc: 'Invite a pubkey' },
  { cmd: '/channel-create', desc: 'Create a new channel' },
  { cmd: '/channel-delete', desc: 'Delete a channel' },
]
const MANAGER_COMMANDS: SlashCommand[] = [
  { cmd: '/mod-add', desc: 'Make a member a moderator' },
  { cmd: '/mod-remove', desc: 'Remove a moderator' },
  { cmd: '/manager-add', desc: 'Make a member a manager (full control)' },
  { cmd: '/manager-remove', desc: 'Remove a manager' },
  { cmd: '/name', desc: 'Set group name' },
  { cmd: '/description', desc: 'Set group description' },
  { cmd: '/validator-add', desc: 'Add a validator: /validator-add <url> (it prepares its own proof)' },
  { cmd: '/validator-remove', desc: 'Remove a validator by URL' },
]
const MANAGER_ONLY_COMMANDS = new Set(MANAGER_COMMANDS.map((command) => command.cmd))

function firstArg(args: string): string {
  return args.trim().split(/\s+/, 1)[0] ?? ''
}

function assertPublished(result: PublishResult | undefined) {
  if (!result || result.total === 0)
    throw new Error('The event was not published: no verified group state or reachable validators.')
  if (result.ok === 0)
    throw new Error(
      `Rejected by all ${result.total} validators${result.error ? `: ${result.error}` : '.'}`,
    )
  if (result.majorityRejected)
    throw new Error(
      `Rejected by ${result.total - result.ok} of ${result.total} validators${result.error ? `: ${result.error}` : '.'}`,
    )
}

function pendingJoinFromLocation(): PendingJoin | null {
  const params = new URLSearchParams(window.location.search)
  const group = params.get('group')?.trim()
  if (!group || !isValidPubkey(group)) return null
  const validatorsParam = params.get('validators') ?? params.get('relays') ?? ''
  const validators = validatorsParam
    .split(/[\s,]+/)
    .map((r) => r.trim())
    .filter(Boolean)
  return { pubkey: group, validators }
}

function chainGroupFromLocation(): string | null {
  const match = window.location.pathname.match(/^\/chain\/([0-9a-f]{64})$/)
  return match && isValidPubkey(match[1]) ? match[1] : null
}

function chainPath(groupPubkey: string): string {
  return `/chain/${groupPubkey}`
}

export default function App() {
  const bracken = useBracken()
  const [showAddGroup, setShowAddGroup] = useState(false)
  const [showMembers, setShowMembers] = useState(false)
  const [showValidators, setShowValidators] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [showGroupInfo, setShowGroupInfo] = useState(false)
  const [showHelp, setShowHelp] = useState(false)
  const [chainGroupPubkey, setChainGroupPubkey] = useState<string | null>(() => chainGroupFromLocation())
  const [sidebarOpen, setSidebarOpen] = useState(false)
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

  const openChain = useCallback((groupPubkey: string) => {
    window.history.pushState(null, '', chainPath(groupPubkey))
    setChainGroupPubkey(groupPubkey)
  }, [])

  const closeChain = useCallback(() => {
    window.history.pushState(null, '', '/')
    setChainGroupPubkey(null)
  }, [])

  useEffect(() => {
    const syncChainRoute = () => setChainGroupPubkey(chainGroupFromLocation())
    window.addEventListener('popstate', syncChainRoute)
    return () => window.removeEventListener('popstate', syncChainRoute)
  }, [])

  useEffect(() => {
    if (!pendingJoin) return
    window.history.replaceState(null, '', window.location.pathname)
  }, [pendingJoin])

  const isAlreadyMember =
    pendingJoin !== null &&
    bracken.groups.some((g) => g.pubkey === pendingJoin.pubkey)
  const chainGroupEntry = chainGroupPubkey
    ? bracken.groups.find((g) => g.pubkey === chainGroupPubkey) ?? null
    : null

  useEffect(() => {
    if (!chainGroupPubkey || !chainGroupEntry || bracken.activeGroup === chainGroupPubkey) return
    bracken.setActiveGroup(chainGroupPubkey)
  }, [bracken, chainGroupEntry, chainGroupPubkey])

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

  const managers = useMemo(() => {
    return bracken.state?.managers ?? new Set<string>()
  }, [bracken.state])
  const mods = useMemo(() => {
    return bracken.state?.mods ?? new Set<string>()
  }, [bracken.state])

  const [selectedChannels, setSelectedChannels] = useState<Record<string, string>>({})
  const storedSelectedChannel = bracken.activeGroup
    ? selectedChannels[bracken.activeGroup] ?? bracken.state?.chatSettings.default_channel ?? ''
    : ''

  const channels = useMemo(() => {
    if (!bracken.state) return [] as Channel[]
    return [...bracken.state.channels.values()].sort((a, b) => a.position - b.position || a.name.localeCompare(b.name))
  }, [bracken.state])
  const defaultChannel = bracken.state?.chatSettings.default_channel ?? ''
  const selectedChannel = bracken.state?.channels.has(storedSelectedChannel)
    ? storedSelectedChannel
    : bracken.state?.channels.has(defaultChannel)
      ? defaultChannel
      : channels[0]?.id ?? ''

  const isViewerManager = bracken.identity ? managers.has(bracken.identity.publicKey) : false
  const isViewerMod = bracken.identity ? mods.has(bracken.identity.publicKey) : false
  const isViewerAdmin = isViewerManager || isViewerMod
  const slashCommands = useMemo(() => {
    return [
      ...USER_COMMANDS,
      ...(isViewerAdmin ? MOD_COMMANDS : []),
      ...(isViewerManager ? MANAGER_COMMANDS : []),
    ]
  }, [isViewerAdmin, isViewerManager])

  if (bracken.loading) {
    return (
      <div className={styles.emptyState}>
        <FernLogo size={28} />
      </div>
    )
  }

  if (chainGroupPubkey && !chainGroupEntry) {
    return (
      <div className={styles.emptyState}>
        <p className={styles.emptyStateTitle}>You're not in that group</p>
        <button className={styles.secondaryBtn} onClick={closeChain}>Back to chat</button>
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
  const activeValidatorCount = bracken.state?.validatorSet.validators.length
    ?? activeGroupEntry?.validators.length
    ?? 0

  if (chainGroupPubkey && chainGroupEntry && activeGroupEntry?.pubkey !== chainGroupPubkey) {
    return (
      <div className={styles.emptyState}>
        <FernLogo size={28} />
      </div>
    )
  }

  if (chainGroupPubkey && chainGroupEntry && activeGroupEntry?.pubkey === chainGroupPubkey) {
    return (
      <Suspense fallback={<div className={styles.emptyState}><FernLogo size={28} /></div>}>
        <ChainViewer
          groupName={bracken.state?.metadata.name || chainGroupEntry.name}
          groupPubkey={chainGroupEntry.pubkey}
          events={bracken.events}
          validatorCount={activeValidatorCount}
          onClose={closeChain}
        />
      </Suspense>
    )
  }

  const totalValidators = bracken.state?.validatorSet.validators.length
    || activeGroupEntry?.validators.length
    || 0
  const onlineValidators = bracken.validatorConns.filter((connection) => connection.connected).length
  const validatorQuorum = bracken.state
    ? quorumFor(bracken.state.validatorSet)
    : totalValidators
  const validatorCountClass =
    validatorQuorum > 0 && onlineValidators > validatorQuorum
      ? styles.validatorCountGreen
      : validatorQuorum > 0 && onlineValidators === validatorQuorum
        ? styles.validatorCountAmber
        : styles.validatorCountRed
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
                  className={`${styles.validatorCountBadge} ${validatorCountClass}`}
                  onClick={() => setShowValidators(true)}
                  title={`${onlineValidators} of ${totalValidators} validator${totalValidators === 1 ? '' : 's'} online`}
                >
                  {onlineValidators}/{totalValidators}
                </button>
              </div>
            </div>

            {activeValidatorCount > 0 && activeValidatorCount < 4 && (
              <div className={styles.smallSetWarning} role="alert">
                Warning: this group uses {activeValidatorCount}-validator unanimous small-set
                mode. Every validator must participate; any unavailable validator halts consensus.
              </div>
            )}

            <MessageList
              events={bracken.events}
              rejectedIds={rejectedIds}
              connectedEventIds={acceptedEventIds}
              managers={managers}
              mods={mods}
              joined={joinedSet}
              nicknames={nicknames}
              banned={bannedSet}
              deliveries={bracken.messageDeliveries}
              channelNames={channelNames}
              viewerPubkey={userPubkey}
              selectedChannel={selectedChannel}
              onAdminAction={async (type, target, extra) => {
                assertPublished(await bracken.adminAction(type, target, extra))
              }}
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
                  assertPublished(await bracken.setNickname(args))
                } else if (isViewerAdmin && cmd === '/kick') {
                  assertPublished(await bracken.adminAction('kick', firstArg(args)))
                } else if (isViewerAdmin && cmd === '/ban') {
                  const target = firstArg(args)
                  const reason = args.trim().slice(target.length).trim()
                  assertPublished(await bracken.adminAction('ban', target, { reason, until: null }))
                } else if (isViewerAdmin && cmd === '/unban') {
                  assertPublished(await bracken.adminAction('unban', firstArg(args)))
                } else if (isViewerAdmin && cmd === '/invite') {
                  assertPublished(await bracken.adminAction('invite', firstArg(args)))
                } else if (isViewerManager && cmd === '/mod-add') {
                  assertPublished(await bracken.adminAction('chat.mod_add', firstArg(args)))
                } else if (isViewerManager && cmd === '/mod-remove') {
                  assertPublished(await bracken.adminAction('chat.mod_remove', firstArg(args)))
                } else if (isViewerManager && cmd === '/manager-add') {
                  assertPublished(await bracken.adminAction('chat.manager_add', firstArg(args)))
                } else if (isViewerManager && cmd === '/manager-remove') {
                  assertPublished(await bracken.adminAction('chat.manager_remove', firstArg(args)))
                } else if (isViewerManager && cmd === '/name' && args.trim()) {
                  assertPublished(await bracken.adminAction('metadata_update', '', { name: args.trim() }))
                } else if (isViewerManager && cmd === '/description') {
                  assertPublished(await bracken.adminAction('metadata_update', '', { description: args.trim() }))
                } else if (isViewerAdmin && cmd === '/channel-create' && args.trim()) {
                  assertPublished(await bracken.adminAction('chat.channel_create', '', { id: randomHexId(), name: args.trim() }))
                } else if (isViewerAdmin && cmd === '/channel-delete' && args.trim()) {
                  const channel = [...(bracken.state?.channels.values() ?? [])].find((ch) => ch.name === args.trim() || ch.id === args.trim())
                  if (channel) assertPublished(await bracken.adminAction('chat.channel_delete', '', { id: channel.id, name: channel.name }))
                } else if (isViewerManager && cmd === '/validator-add') {
                  const url = firstArg(args)
                  if (!url)
                    throw new Error('Usage: /validator-add <validator-url> — the validator syncs its own history and signs its own readiness proof.')
                  assertPublished(await bracken.addValidatorByUrl(url))
                } else if (isViewerManager && cmd === '/validator-remove') {
                  const url = firstArg(args)
                  if (!url) throw new Error('Usage: /validator-remove <validator-url>')
                  assertPublished(await bracken.removeValidatorByUrl(url))
                } else if (cmd === '/nickname') {
                  throw new Error('Usage: /nickname <name>')
                } else if (!isViewerAdmin) {
                  throw new Error(`Only moderators and managers can use ${cmd}.`)
                } else if (MANAGER_ONLY_COMMANDS.has(cmd)) {
                  throw new Error(`Only managers can use ${cmd}.`)
                } else if (cmd === '/channel-delete') {
                  throw args.trim()
                    ? new Error(`No channel named "${args.trim()}" exists in this group.`)
                    : new Error('Usage: /channel-delete <channel-name>')
                } else {
                  throw new Error(`Missing arguments for ${cmd}.`)
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
          onAdminAction={async (type, target, extra) => {
            assertPublished(await bracken.adminAction(type, target, extra))
          }}
        />
      )}
      {showValidators && (
        <ValidatorDrawer
          validatorConns={bracken.validatorConns}
          validatorSet={bracken.state?.validatorSet ?? null}
          peerNotices={bracken.peerNotices}
          activeUrls={bracken.activeUrls}
          onFetchStatus={bracken.fetchValidatorStatus}
          onFetchNotice={bracken.fetchValidatorNotice}
          onClose={() => setShowValidators(false)}
        />
      )}
      {showGroupInfo && activeGroupEntry && (
        <GroupInfoModal
          name={bracken.state?.metadata.name || activeGroupEntry.name}
          pubkey={activeGroupEntry.pubkey}
          description={bracken.state?.metadata.description ?? ''}
          validators={activeGroupEntry.validators}
          validatorCount={activeValidatorCount}
          onViewChain={() => {
            setShowGroupInfo(false)
            openChain(activeGroupEntry.pubkey)
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
          fPlusOneMode={bracken.fPlusOneMode}
          onClose={() => setShowSettings(false)}
          onSetNickname={async (name) => {
            assertPublished(await bracken.setNickname(name))
          }}
          onSetFPlusOneMode={bracken.setFPlusOneMode}
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
              supports decentralized, censorship-resistant communication with
              per-group Byzantine-fault-tolerant validator consensus.
            </p>
            <p>
              Everything runs client-side: identity keys, message verification,
              commit certificates, group state, and event validation happen in your browser.
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
