import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { Commit, SyncReady, Validator, ValidatorSet } from '../fern/bft'
import {
  isSmallUnanimousValidatorSet, validateValidatorSet, validatorQuorum,
  verifyCommitEvidence, verifyValidatorTransition,
} from '../fern/bft'
import type { Keypair } from '../fern/crypto'
import { generateKeypair, keypairFromSeed } from '../fern/crypto'
import {
  advanceHead, clearLocalData, getCommits, getGroupEvents, getIdentity, getMeta,
  putCommit, putEvent, putIngressReceipt, putPendingEvent, putValidatorPin, saveIdentity, setMeta,
} from '../fern/db'
import type { FernEvent } from '../fern/events'
import { buildEvent, canonicalJson, toWireEvent, verifyEvent } from '../fern/events'
import { log, shortId } from '../fern/logger'
import type { ValidatorStatus } from '../fern/validator'
import type { OperatorNotice } from '../fern/validator'
import {
  ValidatorClient, parseGroupAddress, validatorReconnectDelay, verifyIngressReceipt,
} from '../fern/validator'
import type { GroupState } from '../fern/state'
import { computeStateRoot, deriveGroupState } from '../fern/state'
import { randomHexId, sha256Hex } from '../fern/utils'

export interface GroupEntry {
  pubkey: string
  name: string
  validators: string[]
}

export interface ValidatorConnection {
  url: string
  client?: ValidatorClient
  connected: boolean
  reconnecting: boolean
  pubkey: string
  name?: string
  status?: ValidatorStatus
  notice?: OperatorNotice
  peerNotices?: OperatorNotice[]
}

export interface MessageDelivery {
  state: 'sending' | 'failed'
  ok: number
  total: number
  error?: string
  majorityRejected?: boolean
}

export interface PublishResult {
  ok: number
  total: number
  error?: string
  majorityRejected: boolean
}

interface LocalHead {
  height: number
  blockHash: string
  historyRoot: string
  stateRoot: string
  chainId: string
  logicalBytes: number
}

function positionEvents(commit: Commit): FernEvent[] {
  const events = [
    ...commit.block.candidate.events,
    ...(commit.block.candidate.governance ? [commit.block.candidate.governance] : []),
  ]
  return events.map((event, position) => ({
    ...event,
    bft: {
      status: 'finalized' as const,
      height: commit.height,
      position,
      certifiedTimeMs: commit.block.certified_times_ms[position],
    },
  }))
}

function sameStrings(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index])
}

function propagationThreshold(n: number): number {
  return Math.floor((n - 1) / 3) + 1
}

async function initialHead(genesis: FernEvent): Promise<LocalHead> {
  const result = deriveGroupState([genesis])
  if (!result.state) throw new Error('Invalid BFT genesis state')
  return {
    height: 0,
    blockHash: genesis.id,
    historyRoot: await sha256Hex(canonicalJson(['fern-bft-history', genesis.id])),
    stateRoot: await computeStateRoot(result.state),
    chainId: result.state.chainId,
    logicalBytes: canonicalJson(toWireEvent(genesis)).length,
  }
}

async function verifyNextCommit(
  currentEvents: FernEvent[],
  head: LocalHead,
  commit: Commit,
): Promise<{ events: FernEvent[]; head: LocalHead; state: GroupState }> {
  const derived = deriveGroupState(currentEvents)
  if (!derived.state) throw new Error('Cannot verify a commit without genesis')
  await verifyCommitEvidence(commit, derived.state.validatorSet)
  const block = commit.block
  if (
    block.height !== head.height + 1 || block.candidate.previous_block_hash !== head.blockHash ||
    block.candidate.previous_state_root !== head.stateRoot ||
    block.previous_history_root !== head.historyRoot || block.chain_id !== head.chainId
  ) throw new Error('Commit does not extend the verified local checkpoint')
  if (block.candidate.governance?.type === 'validator_update') {
    verifyValidatorTransition(
      block.candidate.governance,
      derived.state.validatorSet,
      block.group,
      block.chain_id,
      head.height,
      head.blockHash,
      head.historyRoot,
      head.logicalBytes,
    )
  }
  const ids = new Set(positionEvents(commit).map((event) => event.id))
  const merged = [...currentEvents.filter((event) => !ids.has(event.id)), ...positionEvents(commit)]
  const next = deriveGroupState(merged)
  if (!next.state || next.rejected.length > 0) throw new Error('Committed application transition is invalid')
  const stateRoot = await computeStateRoot(next.state)
  if (stateRoot !== block.state_root) throw new Error('Committed state root mismatch')
  return {
    events: merged,
    state: next.state,
    head: {
      height: block.height,
      blockHash: block.id,
      historyRoot: block.history_root,
      stateRoot,
      chainId: block.chain_id,
      logicalBytes: head.logicalBytes + canonicalJson(commit).length,
    },
  }
}

async function connect(url: string): Promise<ValidatorClient> {
  const client = new ValidatorClient(url)
  await client.connect()
  return client
}

function waitForRetry(delayMs: number, signal: AbortSignal): Promise<void> {
  if (signal.aborted) return Promise.resolve()
  return new Promise((resolve) => {
    const finish = () => {
      window.clearTimeout(timer)
      signal.removeEventListener('abort', finish)
      resolve()
    }
    const timer = window.setTimeout(finish, delayMs)
    signal.addEventListener('abort', finish, { once: true })
  })
}

export function useBracken() {
  const [identity, setIdentity] = useState<Keypair | null>(null)
  const [groups, setGroups] = useState<GroupEntry[]>([])
  const [activeGroup, setActiveGroup] = useState<string | null>(null)
  const [events, setEvents] = useState<FernEvent[]>([])
  const [state, setState] = useState<GroupState | null>(null)
  const [connStates, setConnStates] = useState<ValidatorConnection[]>([])
  const [defaultNickname, setDefaultNicknameState] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [messageDeliveries, setMessageDeliveries] = useState<Record<string, MessageDelivery>>({})
  const [peerNotices, setPeerNotices] = useState<Record<string, OperatorNotice>>({})
  const [fPlusOneMode, setFPlusOneModeState] = useState(false)
  const clientsRef = useRef<Map<string, ValidatorClient>>(new Map())
  const commitQueues = useRef<Map<string, Promise<void>>>(new Map())
  const [activeUrls, setActiveUrls] = useState<Set<string>>(new Set())

  const recomputeActive = useCallback((states: ValidatorConnection[]) => {
    if (!fPlusOneMode) return
    const connected = states.filter((c) => c.connected).map((c) => c.url)
    const threshold = propagationThreshold(states.length || 1)
    setActiveUrls((current) => {
      const next = new Set(current)
      for (const url of [...next]) {
        if (!connected.includes(url)) next.delete(url)
      }
      const pool = connected.filter((u) => !next.has(u))
      while (next.size < threshold && pool.length > 0) {
        const i = Math.floor(Math.random() * pool.length)
        next.add(pool[i])
        pool.splice(i, 1)
      }
      return next
    })
  }, [fPlusOneMode])

  useEffect(() => {
    recomputeActive(connStates)
  }, [connStates, recomputeActive])

  const validatorConns = useMemo(() => {
    const entry = groups.find((group) => group.pubkey === activeGroup)
    const current = new Map(connStates.map((connection) => [connection.url, connection]))
    return (entry?.validators ?? []).map((url) => current.get(url) ?? {
      url, connected: false, reconnecting: true, pubkey: '',
    })
  }, [groups, activeGroup, connStates])

  const refresh = useCallback(async (group: string) => {
    const stored = await getGroupEvents(group)
    const derived = deriveGroupState(stored)
    setEvents(stored)
    setState(derived.state)
  }, [])

  const applyCommit = useCallback(async (group: string, commit: Commit) => {
    const previous = commitQueues.current.get(group) ?? Promise.resolve()
    const next = previous.then(async () => {
      const currentEvents = await getGroupEvents(group)
      const genesis = currentEvents.find((event) => event.type === 'genesis')
      if (!genesis) throw new Error('Missing genesis')
      const storedHead = await getMeta<LocalHead>(`bft-head:${group}`) ?? await initialHead(genesis)
      if (commit.height <= storedHead.height) return
      log.debug('consensus', 'verifying pushed commit', {
        group: shortId(group), height: commit.height, round: commit.round,
        block: shortId(commit.block.id), events: positionEvents(commit).length,
      })
      const verified = await verifyNextCommit(currentEvents, storedHead, commit)
      await putCommit(commit)
      await advanceHead(group, verified.head)
      setEvents(verified.events)
      setState(verified.state)
      const validatorUrls = verified.state.validatorSet.validators.map((validator) => validator.url)
      setGroups((current) => {
        const existing = current.find((entry) => entry.pubkey === group)
        if (!existing || sameStrings(existing.validators, validatorUrls)) return current
        const updated = current.map((entry) => entry.pubkey === group
          ? { ...entry, validators: validatorUrls } : entry)
        void setMeta('groups', updated)
        return updated
      })
      setMessageDeliveries((current) => {
        const copy = { ...current }
        for (const event of positionEvents(commit)) delete copy[event.id]
        return copy
      })
      log.info('consensus', 'block finalized', {
        group: shortId(group), epoch: verified.state.validatorSet.epoch,
        height: commit.height, round: commit.round, block: shortId(commit.block.id),
        events: positionEvents(commit).length,
      })
    }).catch((error) => log.error('consensus', 'rejected validator commit', {
      group: shortId(group), height: commit.height, block: shortId(commit.block.id),
      reason: String(error),
    }))
    commitQueues.current.set(group, next)
    await next
  }, [])

  const syncEntry = useCallback(async (entry: GroupEntry): Promise<void> => {
    log.info('sync', 'group sync starting', {
      group: shortId(entry.pubkey), validators: entry.validators.length,
    })
    const reachable: {
      client: ValidatorClient
      status: Awaited<ReturnType<ValidatorClient['status']>>
    }[] = []
    const probed = await Promise.allSettled(entry.validators.map(async (url) => {
      const client = await connect(url)
      const status = await client.status(entry.pubkey)
      return { url, client, status }
    }))
    probed.forEach((result, index) => {
      const url = entry.validators[index]
      if (result.status === 'fulfilled') {
        reachable.push({ client: result.value.client, status: result.value.status })
        log.debug('sync', 'validator status verified', {
          group: shortId(entry.pubkey), url, validator: shortId(result.value.status.validator),
          epoch: result.value.status.epoch, height: result.value.status.height,
          block: shortId(result.value.status.block_hash),
        })
      } else {
        log.warn('sync', 'validator unavailable during sync', { url, reason: String(result.reason) })
      }
    })
    if (reachable.length === 0) throw new Error('No group validator is reachable')
    try {
      const byHeight = new Map<number, Set<string>>()
      for (const item of reachable) {
        const hashes = byHeight.get(item.status.height) ?? new Set<string>()
        hashes.add(item.status.block_hash)
        byHeight.set(item.status.height, hashes)
      }
      if ([...byHeight.values()].some((hashes) => hashes.size > 1))
        throw new Error('Validators expose conflicting finalized checkpoints')

      let currentEvents = await getGroupEvents(entry.pubkey)
      let genesis = currentEvents.find((event) => event.type === 'genesis')
      if (!genesis) {
        genesis = await reachable[0].client.getGenesis(entry.pubkey) ?? undefined
        if (!genesis) throw new Error('Validator did not return genesis')
        await verifyEvent(genesis)
        await putEvent(genesis)
        currentEvents = [genesis]
        log.info('sync', 'genesis verified', {
          group: shortId(entry.pubkey), event: shortId(genesis.id),
        })
      }
      let head = await initialHead(genesis)
      const localCommits = await getCommits(entry.pubkey)
      const localPending = currentEvents.filter((event) => event.bft?.status === 'pending')
      let rebuiltEvents = [genesis]
      for (const commit of localCommits) {
        const verified = await verifyNextCommit(rebuiltEvents, head, commit)
        // Re-persist verified commits so an event that a late pending push
        // downgraded back to pending is restored to finalized.
        await putCommit(commit)
        rebuiltEvents = verified.events
        head = verified.head
      }
      log.debug('sync', 'local chain rebuilt', {
        group: shortId(entry.pubkey), height: head.height, commits: localCommits.length,
      })
      currentEvents = [...rebuiltEvents, ...localPending]
      const source = reachable.sort((a, b) => b.status.height - a.status.height)[0]
      while (head.height < source.status.height) {
        const commits = await source.client.getCommits(entry.pubkey, head.height + 1)
        if (commits.length === 0) throw new Error('Validator omitted committed history')
        log.debug('sync', 'commit page received', {
          group: shortId(entry.pubkey), url: source.client.url,
          fromHeight: head.height + 1, count: commits.length, target: source.status.height,
        })
        for (const commit of commits) {
          if (commit.height > source.status.height) break
          const verified = await verifyNextCommit(currentEvents, head, commit)
          await putCommit(commit)
          currentEvents = verified.events
          head = verified.head
          log.debug('sync', 'commit verified', {
            group: shortId(entry.pubkey), height: commit.height,
            block: shortId(commit.block.id), events: positionEvents(commit).length,
          })
        }
      }
      if (head.height === source.status.height && head.blockHash !== source.status.block_hash)
        throw new Error('Downloaded checkpoint mismatch')
      const verifiedHashes = new Map<number, string>([[0, genesis.id]])
      for (const commit of await getCommits(entry.pubkey)) verifiedHashes.set(commit.height, commit.block.id)
      for (const item of reachable) {
        if (item.status.chain_id !== head.chainId ||
          verifiedHashes.get(item.status.height) !== item.status.block_hash)
          throw new Error(`Validator ${item.status.validator} exposes a conflicting finalized checkpoint`)
      }
      await advanceHead(entry.pubkey, head)
      const synced = deriveGroupState(currentEvents)
      if (synced.state) {
        if (isSmallUnanimousValidatorSet(synced.state.validatorSet)) {
          log.warn('consensus', 'participating in unanimous small-set mode', {
            group: shortId(entry.pubkey),
            validators: synced.state.validatorSet.validators.length,
            quorum: validatorQuorum(synced.state.validatorSet),
            faultTolerance: 0,
          })
        }
        const validatorUrls = synced.state.validatorSet.validators.map((validator) => validator.url)
        setGroups((current) => {
          const existing = current.find((value) => value.pubkey === entry.pubkey)
          if (!existing || sameStrings(existing.validators, validatorUrls)) return current
          const updated = current.map((value) => value.pubkey === entry.pubkey
            ? { ...value, name: synced.state!.metadata.name, validators: validatorUrls } : value)
          void setMeta('groups', updated)
          return updated
        })
      }
      await refresh(entry.pubkey)
      log.info('sync', 'group sync complete', {
        group: shortId(entry.pubkey), height: head.height,
        reachable: reachable.length, validators: entry.validators.length,
      })
    } finally {
      await Promise.all(reachable.map(({ client }) => client.close()))
    }
  }, [refresh])

  useEffect(() => {
    void (async () => {
      const stored = await getIdentity()
      if (stored) setIdentity(keypairFromSeed(stored.seed))
      const storedGroups = await getMeta<Array<GroupEntry & { relays?: string[] }>>('groups') ?? []
      const savedGroups = storedGroups.map((group) => ({
        pubkey: group.pubkey,
        name: group.name,
        validators: group.validators ?? group.relays ?? [],
      }))
      if (storedGroups.some((group) => !group.validators)) void setMeta('groups', savedGroups)
      setGroups(savedGroups)
      setDefaultNicknameState(await getMeta<string>('defaultNickname') ?? null)
      setFPlusOneModeState(await getMeta<boolean>('fPlusOneMode') === true)
      setActiveGroup(savedGroups[0]?.pubkey ?? null)
      setLoading(false)
      log.info('client', 'local client state loaded', {
        identity: stored ? shortId(stored.pubkey) : 'none', groups: savedGroups.length,
      })
    })()
  }, [])

  useEffect(() => {
    // Intentional synchronous reset: clearing derived state when no group is
    // active terminates in one extra render and cannot cascade further.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!activeGroup) { setEvents([]); setState(null); return }
    const entry = groups.find((group) => group.pubkey === activeGroup)
    if (!entry) return
    const abortController = new AbortController()
    const { signal } = abortController
    const clients = clientsRef.current
    let syncPromise: Promise<void> | null = null

    const ensureSynchronized = async (): Promise<void> => {
      if (!syncPromise) {
        syncPromise = syncEntry(entry)
          .finally(() => { syncPromise = null })
      }
      await syncPromise
    }

    const updateConnection = (
      url: string,
      value: Partial<ValidatorConnection>,
    ): void => {
      if (signal.aborted) return
      setConnStates((current) => {
        const previous = current.find((connection) => connection.url === url)
        const next = [
          ...current.filter((connection) => connection.url !== url),
          {
            url,
            connected: false,
            reconnecting: true,
            pubkey: '',
            ...previous,
            ...value,
          },
        ]
        return next
      })
    }

    const supervise = async (url: string): Promise<void> => {
      let attempt = 0
      while (!signal.aborted) {
        updateConnection(url, { connected: false, reconnecting: true })
        const client = new ValidatorClient(url)
        try {
          await client.connect()
          const metadata = await client.fetchMetadata()
          client.onPending(async (event, receipt) => {
            try {
              await verifyEvent(event)
              const currentState = deriveGroupState(await getGroupEvents(entry.pubkey)).state
              const currentValidator = currentState?.validatorSet.validators.find(
                (validator) => validator.url === url,
              )
              if (!currentState || !currentValidator || currentValidator.pubkey !== metadata.pubkey ||
                !verifyIngressReceipt(receipt) || receipt.event_id !== event.id ||
                receipt.validator !== metadata.pubkey || receipt.group !== entry.pubkey ||
                receipt.chain_id !== currentState.chainId ||
                receipt.epoch !== currentState.validatorSet.epoch) {
                log.warn('ingress', 'discarded invalid pending event evidence', {
                  group: shortId(entry.pubkey), event: shortId(event.id), url,
                })
                return
              }
              await putIngressReceipt(receipt)
              await putPendingEvent(event)
              log.info('ingress', 'pending event verified', {
                group: shortId(event.group), event: shortId(event.id), type: event.type,
                author: shortId(event.author), seq: event.seq,
                validator: shortId(receipt.validator), url,
              })
              if (event.group === activeGroup) await refresh(activeGroup)
            } catch (error) {
              log.warn('ingress', 'rejected pending event push', { url, reason: String(error) })
            }
          })
          client.onCommit((commit) => {
            log.debug('consensus', 'commit push received', {
              group: shortId(entry.pubkey), url, height: commit.height,
              block: shortId(commit.block.id),
            })
            void applyCommit(entry.pubkey, commit)
          })
          await client.subscribe(entry.pubkey)
          if (signal.aborted) break

          // Authorize the endpoint against the local verified validator set so a
          // healthy validator connects immediately. Only fall back to a blocking
          // sync when the local state cannot authorize it (a brand-new group, or
          // an epoch transition that rebound this endpoint while we were offline).
          const keyAuthorized = async (): Promise<boolean> => {
            const localState = deriveGroupState(await getGroupEvents(entry.pubkey)).state
            const expected = localState?.validatorSet.validators.find(
              (validator) => validator.url === url,
            )
            return expected?.pubkey === metadata.pubkey
          }
          if (await keyAuthorized()) {
            // Catch up any missed history without blocking the connection.
            void ensureSynchronized().catch((error) => log.warn('sync', 'background sync failed', {
              group: shortId(entry.pubkey), url, reason: String(error),
            }))
          } else {
            await ensureSynchronized()
            if (signal.aborted) break
            // A finalized epoch transition may deliberately rebind an endpoint
            // to a new key; the committed validator set is the authority here.
            if (!await keyAuthorized())
              throw new Error('validator endpoint key does not match finalized group state')
          }
          await putValidatorPin(url, metadata.pubkey)
          clients.set(url, client)
          if (metadata.peer_notices) {
            setPeerNotices((current) => {
              const next = { ...current }
              for (const notice of metadata.peer_notices!) {
                next[notice.validator] = notice
              }
              return next
            })
          }
          updateConnection(url, {
            client, connected: true, reconnecting: false, pubkey: metadata.pubkey,
            name: metadata.name || url, notice: metadata.notice,
            peerNotices: metadata.peer_notices,
          })
          void client.status(entry.pubkey)
            .then((st) => updateConnection(url, { status: st }))
            .catch(() => { /* group not hosted on this validator yet */ })
          log.info('transport', 'validator subscribed', {
            group: shortId(entry.pubkey), url, validator: shortId(metadata.pubkey),
          })
          attempt = 0
          await client.waitForClose()
          if (signal.aborted) break
          updateConnection(url, {
            client: undefined, connected: false, reconnecting: true,
          })
          log.info('transport', 'validator reconnect scheduled', {
            group: shortId(entry.pubkey), url,
          })
        } catch (error) {
          if (signal.aborted) break
          log.warn('transport', 'validator subscription failed', {
            group: shortId(entry.pubkey), url, reason: String(error),
          })
          updateConnection(url, {
            client: undefined, connected: false, reconnecting: true,
          })
        } finally {
          if (clients.get(url) === client) clients.delete(url)
          await client.close()
        }
        if (signal.aborted) break
        const delayMs = validatorReconnectDelay(attempt)
        log.debug('transport', 'waiting before validator reconnect', {
          group: shortId(entry.pubkey), url, delayMs, attempt: attempt + 1,
        })
        attempt += 1
        await waitForRetry(delayMs, signal)
      }
    }

    setConnStates(entry.validators.map((url) => ({
      url, connected: false, reconnecting: true, pubkey: '',
    })))
    // Render the locally verified snapshot immediately; supervisors catch up any
    // missed history in the background once they connect.
    void refresh(entry.pubkey)
    for (const url of entry.validators) void supervise(url)

    return () => {
      abortController.abort()
      for (const client of clients.values()) void client.close()
      clients.clear()
      setConnStates([])
    }
  }, [activeGroup, groups, fPlusOneMode, applyCommit, refresh, syncEntry])

  const importIdentity = useCallback(async (seed: string) => {
    const keypair = keypairFromSeed(seed)
    await saveIdentity({ pubkey: keypair.publicKey, seed: keypair.seed, secretKey: keypair.secretKey })
    setIdentity(keypair)
  }, [])

  const logout = useCallback(async () => {
    for (const client of clientsRef.current.values()) await client.close()
    await clearLocalData()
    setIdentity(null); setGroups([]); setActiveGroup(null); setEvents([]); setState(null)
  }, [])

  const setDefaultNickname = useCallback(async (name: string | null) => {
    setDefaultNicknameState(name)
    await setMeta('defaultNickname', name)
  }, [])

  const setFPlusOneMode = useCallback(async (value: boolean) => {
    setFPlusOneModeState(value)
    await setMeta('fPlusOneMode', value)
  }, [])

  const publish = useCallback(async (event: FernEvent, entry: GroupEntry) => {
    log.info('ingress', 'publishing event', {
      group: shortId(event.group), event: shortId(event.id), type: event.type,
      author: shortId(event.author), seq: event.seq, validators: entry.validators.length,
    })
    const picked = fPlusOneMode ? [...activeUrls] : entry.validators
    const targets = picked.length > 0 ? picked : entry.validators
    setMessageDeliveries((current) => ({
      ...current, [event.id]: { state: 'sending', ok: 0, total: targets.length },
    }))
    let ok = 0
    let error = ''
    const localState = deriveGroupState(await getGroupEvents(event.group)).state
    if (!localState) throw new Error('Cannot publish without verified group state')
    await Promise.all(targets.map(async (url) => {
      let client = clientsRef.current.get(url)
      let ephemeral = false
      try {
        if (!client?.isConnected) { client = await connect(url); ephemeral = true }
        const expected = localState.validatorSet.validators.find((validator) => validator.url === url)
        if (!expected) throw new Error('Endpoint is not in the finalized validator set')
        const metadata = client.validatorPubkey
          ? { pubkey: client.validatorPubkey }
          : await client.fetchMetadata()
        if (metadata.pubkey !== expected.pubkey) throw new Error('Validator endpoint key mismatch')
        const receipt = await client.publish(event)
        if (!verifyIngressReceipt(receipt) || receipt.event_id !== event.id ||
          receipt.validator !== expected.pubkey || receipt.group !== event.group ||
          receipt.chain_id !== localState.chainId || receipt.epoch !== localState.validatorSet.epoch)
          throw new Error('Invalid ingress receipt')
        await putIngressReceipt(receipt)
        ok += 1
        log.debug('ingress', 'ingress receipt verified', {
          group: shortId(event.group), event: shortId(event.id), url,
          validator: shortId(receipt.validator), firstSeenMs: receipt.first_seen_ms,
        })
      } catch (caught) {
        error ||= String(caught)
        log.warn('ingress', 'validator rejected event', {
          group: shortId(event.group), event: shortId(event.id), url, reason: String(caught),
        })
      } finally { if (ephemeral) await client?.close() }
    }))
    if (ok > 0) await putPendingEvent(event)
    const total = targets.length
    const majorityRejected = ok > 0 && ok * 2 < total
    setMessageDeliveries((current) => ({
      ...current,
      [event.id]: ok > 0
        ? { state: 'sending', ok, total, error: majorityRejected ? error : undefined, majorityRejected }
        : { state: 'failed', ok, total, error },
    }))
    await refresh(event.group)
    log.info('ingress', 'event publication complete', {
      group: shortId(event.group), event: shortId(event.id), receipts: ok,
      validators: targets.length, propagationConfirmed:
        ok >= localState.validatorSet.fault_tolerance + 1,
    })
    return { ok, total, error: error || undefined, majorityRejected }
  }, [refresh])

  const nextSequence = useCallback((author: string): number | null => {
    if (!state) return null
    const pending = new Set(
      events
        .filter((event) => event.author === author && event.bft?.status === 'pending')
        .map((event) => event.seq),
    )
    let sequence = (state.sequences.get(author) ?? 0) + 1
    while (pending.has(sequence)) sequence += 1
    return sequence
  }, [state, events])

  const sendMessage = useCallback(async (text: string, channel: string): Promise<boolean> => {
    if (!identity || !activeGroup) return false
    const entry = groups.find((group) => group.pubkey === activeGroup)
    const seq = nextSequence(identity.publicKey)
    if (!entry || seq === null) return false
    const event = await buildEvent({
      type: 'chat.message', group: activeGroup, author: identity.publicKey, seq,
      content: { text, channel }, ts: Math.floor(Date.now() / 1000), tags: [],
    }, identity)
    return (await publish(event, entry)).ok > 0
  }, [identity, activeGroup, groups, nextSequence, publish])

  const retryMessage = useCallback(async (eventId: string) => {
    const event = events.find((value) => value.id === eventId)
    const entry = event ? groups.find((group) => group.pubkey === event.group) : undefined
    if (event && entry) await publish(event, entry)
  }, [events, groups, publish])

  const adminAction = useCallback(async (
    type: string, target = '', extra: Record<string, unknown> = {},
  ) => {
    if (!identity || !activeGroup) return
    if (type === 'validator_update')
      throw new Error('Validator changes require sync-ready proofs and are currently CLI-only.')
    const entry = groups.find((group) => group.pubkey === activeGroup)
    const seq = nextSequence(identity.publicKey)
    if (!entry || seq === null) return
    const content = { ...extra }
    if (type === 'invite') { content['invitee'] = target; content['role'] = 'member' }
    else if (['kick', 'ban', 'unban', 'chat.manager_add', 'chat.manager_remove', 'chat.mod_add', 'chat.mod_remove'].includes(type)) content['target'] = target
    const event = await buildEvent({
      type, group: activeGroup, author: identity.publicKey, seq, content,
      ts: Math.floor(Date.now() / 1000), tags: [],
    }, identity)
    return await publish(event, entry)
  }, [identity, activeGroup, groups, nextSequence, publish])

  const createGroup = useCallback(async (
    name: string, validatorUrls: string[], options?: { description?: string; public?: boolean },
  ) => {
    if (!identity) throw new Error('No identity')
    log.info('group', 'group creation starting', { name, validators: validatorUrls.length })
    const discovered: Validator[] = []
    for (const url of [...new Set(validatorUrls)]) {
      const client = await connect(url)
      try {
        const metadata = await client.fetchMetadata()
        discovered.push({ pubkey: metadata.pubkey, url, operator: metadata.name || url })
        log.debug('group', 'validator discovered', {
          url, validator: shortId(metadata.pubkey), operator: metadata.name || url,
        })
      } finally { await client.close() }
    }
    discovered.sort((a, b) => a.pubkey.localeCompare(b.pubkey))
    const faultTolerance = Math.floor((discovered.length - 1) / 3)
    const validatorSet: ValidatorSet = { epoch: 0, fault_tolerance: faultTolerance, validators: discovered }
    validateValidatorSet(validatorSet)
    const groupKeypair = generateKeypair()
    const channel = randomHexId()
    const genesis = await buildEvent({
      type: 'genesis', group: groupKeypair.publicKey, author: identity.publicKey, seq: 0,
      content: {
        chain_id: randomHexId(), name, description: options?.description ?? '',
        public: options?.public ?? true, founder: identity.publicKey,
        'chat.managers': [identity.publicKey], validators: discovered,
        fault_tolerance: faultTolerance, app: 'chat',
        'chat.channels': [{ id: channel, name: 'general', description: '', position: 0 }],
        'chat.default_channel': channel, 'chat.system_channel': channel,
      },
      ts: Math.floor(Date.now() / 1000), tags: [],
    }, identity, groupKeypair)
    let ok = 0
    let error = ''
    await Promise.all(validatorUrls.map(async (url) => {
      const client = await connect(url)
      try {
        await client.bootstrap(genesis)
        ok += 1
        log.debug('group', 'genesis accepted by validator', {
          group: shortId(genesis.group), url,
        })
      } catch (caught) {
        error ||= String(caught)
        log.warn('group', 'validator rejected genesis', { url, reason: String(caught) })
      } finally { await client.close() }
    }))
    if (ok !== validatorUrls.length) return { ok, total: validatorUrls.length, error }
    await putEvent(genesis)
    await setMeta(`bft-head:${genesis.group}`, await initialHead(genesis))
    const entry = {
      pubkey: genesis.group,
      name,
      validators: discovered.map((validator) => validator.url),
    }
    const updated = [...groups, entry]
    setGroups(updated); await setMeta('groups', updated); setActiveGroup(genesis.group)
    log.info('group', 'group created', {
      group: shortId(genesis.group), validators: discovered.length,
      faults: faultTolerance, quorum: validatorQuorum(validatorSet),
    })
    if (isSmallUnanimousValidatorSet(validatorSet)) {
      log.warn('consensus', 'created group in unanimous small-set mode', {
        group: shortId(genesis.group), validators: discovered.length,
        quorum: validatorQuorum(validatorSet), faultTolerance: 0,
      })
    }
    return { ok, total: validatorUrls.length, error: error || undefined }
  }, [identity, groups])

  const joinGroup = useCallback(async (address: string) => {
    if (!identity) throw new Error('Create or import an identity first')
    const parsed = parseGroupAddress(address)
    if (!parsed.groupPubkey || parsed.validators.length === 0) throw new Error('Invalid group address')
    log.info('group', 'group join starting', {
      group: shortId(parsed.groupPubkey), validators: parsed.validators.length,
    })
    const provisional: GroupEntry = {
      pubkey: parsed.groupPubkey,
      name: 'Loading…',
      validators: parsed.validators,
    }
    await syncEntry(provisional)
    const stored = await getGroupEvents(parsed.groupPubkey)
    const derived = deriveGroupState(stored)
    if (!derived.state) throw new Error('Invalid group genesis')
    const entry: GroupEntry = {
      pubkey: parsed.groupPubkey,
      name: derived.state.metadata.name,
      validators: derived.state.validatorSet.validators.map((validator) => validator.url),
    }
    const seq = (derived.state.sequences.get(identity.publicKey) ?? 0) + 1
    const event = await buildEvent({
      type: 'join', group: entry.pubkey, author: identity.publicKey, seq,
      content: {}, ts: Math.floor(Date.now() / 1000), tags: [],
    }, identity)
    await publish(event, entry)
    const updated = [...groups.filter((group) => group.pubkey !== entry.pubkey), entry]
    setGroups(updated); await setMeta('groups', updated); setActiveGroup(entry.pubkey)
    log.info('group', 'group join submitted', {
      group: shortId(entry.pubkey), validators: entry.validators.length,
    })
  }, [identity, groups, publish, syncEntry])

  const setNickname = useCallback(async (name: string) => {
    await setDefaultNickname(name)
    return await adminAction('chat.nickname_set', '', { nickname: name })
  }, [adminAction, setDefaultNickname])

  const updateValidatorSet = useCallback(async (
    newValidators: Validator[],
    readiness: SyncReady[],
  ): Promise<PublishResult> => {
    if (!identity || !activeGroup)
      throw new Error('Cannot update validators without an identity and active group')
    const entry = groups.find((group) => group.pubkey === activeGroup)
    if (!entry) throw new Error('No verified group state available')
    const localState = deriveGroupState(await getGroupEvents(activeGroup)).state
    if (!localState) throw new Error('Cannot update validators without verified group state')
    const head = await getMeta<LocalHead>(`bft-head:${activeGroup}`)
    if (!head) throw new Error('Cannot update validators without a verified checkpoint')
    const faultTolerance = Math.floor((newValidators.length - 1) / 3)
    const nextSet: ValidatorSet = {
      epoch: localState.validatorSet.epoch + 1,
      fault_tolerance: faultTolerance,
      validators: [...newValidators].sort((a, b) => a.pubkey.localeCompare(b.pubkey)),
    }
    validateValidatorSet(nextSet)
    const seq = nextSequence(identity.publicKey)
    if (seq === null) throw new Error('Cannot allocate a sequence without verified group state')
    const event = await buildEvent({
      type: 'validator_update', group: activeGroup, author: identity.publicKey, seq,
      content: {
        validators: nextSet.validators,
        fault_tolerance: faultTolerance,
        readiness,
      },
      ts: Math.floor(Date.now() / 1000), tags: [],
    }, identity)
    // Validate readiness proofs against the current checkpoint before publishing.
    verifyValidatorTransition(
      event, localState.validatorSet, activeGroup, localState.chainId,
      head.height, head.blockHash, head.historyRoot, head.logicalBytes,
    )
    return await publish(event, entry)
  }, [identity, activeGroup, groups, nextSequence, publish])

  const addValidatorByUrl = useCallback(async (url: string): Promise<PublishResult> => {
    const current = state?.validatorSet
    if (!current) throw new Error('No verified validator set available')
    if (!activeGroup) throw new Error('No active group')
    const entry = groups.find((group) => group.pubkey === activeGroup)
    if (!entry) throw new Error('No verified group state available')
    const client = new ValidatorClient(url)
    try {
      await client.connect()
      const metadata = await client.fetchMetadata()
      if (current.validators.some((v) => v.pubkey === metadata.pubkey))
        throw new Error('That validator is already in the set')
      const newValidator: Validator = {
        pubkey: metadata.pubkey, url: client.url, operator: metadata.name || url,
      }
      const sources = current.validators.map((v) => v.url)
      let lastError = 'Validator update failed'
      for (let attempt = 0; attempt < 2; attempt++) {
        // The prospective validator applies its own WoT admission policy,
        // verifies the full history, and signs readiness for the exact
        // checkpoint. It refuses unless enough locally trusted current
        // validators already host the group.
        const readiness = await client.requestReadiness(activeGroup, sources)
        let result: PublishResult | undefined
        try {
          result = await updateValidatorSet([...current.validators, newValidator], [readiness])
        } catch (err) {
          lastError = String(err)
          // Readiness is bound to the exact pre-transition checkpoint. If the
          // group advanced while preparing, refresh and retry once.
          if (attempt === 0 && /readiness|checkpoint/i.test(lastError)) {
            log.warn('group', 'validator update stale, retrying with fresh readiness', {
              group: shortId(activeGroup), reason: lastError,
            })
            await refresh(activeGroup)
            continue
          }
          throw err
        }
        if (result.ok > 0 && !result.majorityRejected) return result
        lastError = result.error
          ?? `Rejected by ${result.total - result.ok} of ${result.total} validators`
        if (attempt === 0 && /readiness|checkpoint/i.test(lastError)) {
          log.warn('group', 'validator update rejected as stale, retrying', {
            group: shortId(activeGroup), reason: lastError,
          })
          await refresh(activeGroup)
          continue
        }
        throw new Error(lastError)
      }
      throw new Error(lastError)
    } finally { await client.close() }
  }, [state, activeGroup, groups, updateValidatorSet, refresh])

  const removeValidatorByUrl = useCallback(async (url: string): Promise<PublishResult> => {
    const current = state?.validatorSet
    if (!current) throw new Error('No verified validator set available')
    const remaining = current.validators.filter((v) => v.url !== url)
    if (remaining.length === current.validators.length)
      throw new Error(`No validator with URL ${url} in the current set`)
    if (remaining.length === 0) throw new Error('Cannot remove the last validator')
    return await updateValidatorSet(remaining, [])
  }, [state, updateValidatorSet])

  const fetchValidatorStatus = useCallback(async (url: string): Promise<ValidatorStatus | null> => {
    if (!activeGroup) return null
    const live = clientsRef.current.get(url)
    if (live?.isConnected) {
      try { return await live.status(activeGroup) } catch { return null }
    }
    let ephemeral: ValidatorClient | null = null
    try {
      ephemeral = await connect(url)
      return await ephemeral.status(activeGroup)
    } catch {
      return null
    } finally {
      if (ephemeral) await ephemeral.close()
    }
  }, [activeGroup])

  const fetchValidatorNotice = useCallback(async (url: string): Promise<OperatorNotice | null> => {
    const live = clientsRef.current.get(url)
    if (live?.isConnected) {
      try { return (await live.fetchMetadata()).notice ?? null } catch { return null }
    }
    let ephemeral: ValidatorClient | null = null
    try {
      ephemeral = await connect(url)
      return (await ephemeral.fetchMetadata()).notice ?? null
    } catch {
      return null
    } finally {
      if (ephemeral) await ephemeral.close()
    }
  }, [])

  const leaveGroup = useCallback(async (group: string) => {
    if (group === activeGroup) await adminAction('leave')
    const updated = groups.filter((entry) => entry.pubkey !== group)
    setGroups(updated); await setMeta('groups', updated); setActiveGroup(updated[0]?.pubkey ?? null)
  }, [activeGroup, adminAction, groups])

  return {
    identity, loading, groups, activeGroup, events, state, defaultNickname,
    validatorConns, peerNotices, messageDeliveries, setActiveGroup, importIdentity, logout,
    joinGroup, sendMessage, retryMessage, createGroup, adminAction,
    setNickname, setDefaultNickname, leaveGroup, updateValidatorSet,
    addValidatorByUrl, removeValidatorByUrl, fetchValidatorStatus, fetchValidatorNotice,
    fPlusOneMode, setFPlusOneMode, activeUrls,
  }
}
