const STYLES = {
  relay: 'color: #6b8e23; font-weight: bold',
  session: 'color: #2267a8; font-weight: bold',
  event: 'color: #9b59b6; font-weight: bold',
  state: 'color: #e67e22; font-weight: bold',
  complete: 'color: #16a085; font-weight: bold',
  db: 'color: #7f8c8d; font-weight: bold',
} as const

type Category = keyof typeof STYLES

function fmt(category: Category, msg: string, ...args: unknown[]): [string, ...unknown[]] {
  return [`%c[bracken:${category}]%c ${msg}`, STYLES[category], '', ...args]
}

function shortId(id: string | undefined): string {
  return id ? id.slice(0, 12) + '…' : '(unknown)'
}

function shortPubkey(pk: string | undefined): string {
  return pk ? pk.slice(0, 12) + '…' : '(unknown)'
}

export const log = {
  // ── relay ──────────────────────────────────────────────────────────
  relayConnect(url: string) {
    console.debug(...fmt('relay', `connecting to ${url}`))
  },
  relayConnected(url: string, pubkey: string) {
    console.debug(...fmt('relay', `connected to ${url} (pubkey ${shortPubkey(pubkey)})`))
  },
  relayConnectFailed(url: string, err: unknown) {
    console.warn(...fmt('relay', `connection failed: ${url}`, err))
  },
  relayClosed(url: string) {
    console.debug(...fmt('relay', `connection closed: ${url}`))
  },
  relayReconnect(url: string, attempt: number, delayMs: number) {
    console.debug(...fmt('relay', `reconnecting ${url} in ${delayMs}ms (attempt ${attempt})`))
  },
  relayMetadata(url: string, meta: { name: string; pubkey: string; software: string; version: string }) {
    console.debug(...fmt('relay', `metadata for ${url}: ${meta.name} (${meta.software} ${meta.version}) pubkey=${shortPubkey(meta.pubkey)}`))
  },
  relayMetadataFailed(url: string, err: unknown) {
    console.warn(...fmt('relay', `metadata fetch failed for ${url}`, err))
  },
  relaySend(url: string, action: string, extra?: string) {
    console.debug(...fmt('relay', `→ ${action} to ${url}${extra ? ` ${extra}` : ''}`))
  },
  relayResponse(url: string, type: string, extra?: string) {
    console.debug(...fmt('relay', `← ${type} from ${url}${extra ? ` ${extra}` : ''}`))
  },
  relayError(url: string, msg: string, err?: unknown) {
    console.error(...fmt('relay', `error on ${url}: ${msg}`, err ?? ''))
  },
  relayTimeout(url: string, expectedType: string, timeoutMs: number) {
    console.warn(...fmt('relay', `timeout waiting for ${expectedType} from ${url} (${timeoutMs}ms)`))
  },
  relayPushEvent(url: string, eventType: string, eventId: string) {
    console.debug(...fmt('relay', `← pushed event ${eventType} ${shortId(eventId)} from ${url}`))
  },
  relayPushGroupStatus(url: string, group: string, setHash: string, count: number) {
    console.debug(...fmt('relay', `← group_status from ${url}: group=${shortPubkey(group)} set_hash=${shortId(setHash)} count=${count}`))
  },
  relayQueueEnqueue(url: string, queueKey: string, depth: number) {
    console.debug(...fmt('relay', `request queued on ${url} [${queueKey}] (depth: ${depth})`))
  },
  relayQueueDequeue(url: string, queueKey: string, waitMs: number) {
    console.debug(...fmt('relay', `request dequeued on ${url} [${queueKey}] after ${waitMs}ms`))
  },

  // ── session (publish, receive, group lifecycle) ────────────────────
  sessionPublish(group: string, eventType: string, eventId: string) {
    console.info(...fmt('session', `publishing ${eventType} to group ${shortPubkey(group)} id=${shortId(eventId)}`))
  },
  sessionPublishResult(group: string, eventId: string, ok: number, total: number) {
    const level = ok === total ? 'info' : ok > 0 ? 'warn' : 'error'
    console[level](...fmt('session', `publish result for ${shortId(eventId)} in ${shortPubkey(group)}: ${ok}/${total} relays accepted`))
  },
  sessionPublishFailed(group: string, eventType: string, err: unknown) {
    console.error(...fmt('session', `publish failed for ${eventType} in group ${shortPubkey(group)}`, err))
  },
  sessionReceive(url: string, eventType: string, eventId: string) {
    console.info(...fmt('session', `received ${eventType} ${shortId(eventId)} from ${url}`))
  },
  sessionVerifyFailed(eventType: string, eventId: string, err: unknown) {
    console.error(...fmt('session', `verify failed for ${eventType} ${shortId(eventId)}`, err))
  },
  sessionJoin(group: string, relays: string[]) {
    console.info(...fmt('session', `joining group ${shortPubkey(group)} via ${relays.length} relays`))
  },
  sessionJoined(group: string, name: string) {
    console.info(...fmt('session', `joined group "${name}" (${shortPubkey(group)})`))
  },
  sessionJoinFailed(err: unknown) {
    console.error(...fmt('session', 'join failed', err))
  },
  sessionLeave(group: string) {
    console.info(...fmt('session', `leaving group ${shortPubkey(group)}`))
  },
  sessionCreateGroup(name: string, groupPubkey: string, relays: string[]) {
    console.info(...fmt('session', `creating group "${name}" (${shortPubkey(groupPubkey)}) on ${relays.length} relays`))
  },
  sessionCreateGroupResult(name: string, ok: number, total: number) {
    console.info(...fmt('session', `create group "${name}" result: ${ok}/${total} relays accepted genesis`))
  },
  sessionAdminAction(type: string, target: string) {
    console.info(...fmt('session', `admin action: ${type}${target ? ` target=${shortPubkey(target)}` : ''}`))
  },
  sessionSetNickname(nickname: string) {
    console.info(...fmt('session', `setting nickname to "${nickname}"`))
  },
  sessionSetActiveGroup(group: string | null) {
    if (group) {
      console.info(
        `%c━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`,
        'color: #2267a8',
      )
      console.info(
        `%c[bracken:session]%c Switching to group ${shortPubkey(group)}`,
        STYLES.session, '', group,
      )
      console.info(
        `%c━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`,
        'color: #2267a8',
      )
    } else {
      console.debug(...fmt('session', 'active group → (none)'))
    }
  },
  sessionLogout() {
    console.info(...fmt('session', 'logout'))
  },

  // ── sync / heal ────────────────────────────────────────────────────
  syncStart(url: string, group: string) {
    console.info(...fmt('session', `sync starting with ${url} for group ${shortPubkey(group)}`))
  },
  syncSetHashMatch(url: string) {
    console.debug(...fmt('session', `set_hash matches with ${url}, in sync`))
  },
  syncSetHashMismatch(url: string, localHash: string, remoteHash: string) {
    console.info(...fmt('session', `set_hash mismatch with ${url}: local=${shortId(localHash)} remote=${shortId(remoteHash)}`))
  },
  syncLockGranted(url: string, ttl?: number) {
    console.debug(...fmt('session', `sync lock granted by ${url}${ttl ? ` ttl=${ttl}s` : ''}`))
  },
  syncLockDenied(url: string, expiresIn?: number) {
    console.debug(...fmt('session', `sync lock denied by ${url}${expiresIn ? ` expires in ${expiresIn}s` : ''}`))
  },
  syncMissingLocally(url: string, count: number) {
    console.info(...fmt('session', `${count} events missing locally from ${url}`))
  },
  syncMissingOnRelay(url: string, count: number) {
    console.info(...fmt('session', `${count} events missing on ${url}`))
  },
  syncFetched(url: string, count: number) {
    console.info(...fmt('session', `fetched ${count} events from ${url}`))
  },
  syncFallbackFullSync(url: string) {
    console.info(...fmt('session', `falling back to full sync from ${url}`))
  },
  syncFallbackResult(url: string, fetched: number) {
    console.info(...fmt('session', `full sync from ${url}: fetched ${fetched} events`))
  },
  syncGroupStatusFailed(url: string, err: unknown) {
    console.warn(...fmt('session', `group_status request failed for ${url}`, err))
  },
  syncGroupStatusVerificationFailed(url: string) {
    console.error(...fmt('session', `group_status verification failed for ${url}`))
  },
  syncComplete(url: string, fetched: number, healed: number) {
    console.info(...fmt('session', `sync with ${url} complete: fetched=${fetched} healed=${healed}`))
  },
  syncGetFailed(url: string, eventId: string, err: unknown) {
    console.warn(...fmt('session', `failed to fetch event ${shortId(eventId)} from ${url}`, err))
  },

  healBatch(url: string, count: number) {
    console.info(...fmt('session', `healing ${count} events to ${url}`))
  },
  healBatchResult(url: string, healed: number, total: number) {
    console.info(...fmt('session', `heal result for ${url}: ${healed}/${total} events healed`))
  },
  healTrustedStart(url: string, eventCount: number) {
    console.info(...fmt('session', `trusted heal starting: ${eventCount} events to ${url}`))
  },
  healTrustedChallenge(url: string, witnessCount: number, threshold: string) {
    console.debug(...fmt('session', `got heal challenge from ${url}: ${witnessCount} witnesses, threshold=${threshold}`))
  },
  healTrustedChallengeVerifyFailed() {
    console.warn(...fmt('session', 'heal challenge verification failed'))
  },
  healTrustedChallengeFailed(url: string, err: unknown) {
    console.warn(...fmt('session', `getHealChallenge failed from ${url}, falling back to slow heal`, err))
  },
  healTrustedWitnessConnected(url: string, witness: string) {
    console.debug(...fmt('session', `connected to witness ${url} (${shortPubkey(witness)})`))
  },
  healTrustedWitnessConnectFailed(url: string) {
    console.warn(...fmt('session', `failed to connect to witness ${url}`))
  },
  healTrustedWitnessPubkeyMismatch(url: string, expected: string, got: string) {
    console.warn(...fmt('session', `witness ${url} pubkey mismatch: expected ${shortPubkey(expected)}, got ${shortPubkey(got)}`))
  },
  healTrustedHostAttestation(witness: string, hosts: boolean) {
    console.debug(...fmt('session', `witness ${shortPubkey(witness)} hosts=${hosts}`))
  },
  healTrustedHostAttestationFailed(witness: string, err: unknown) {
    console.warn(...fmt('session', `host attestation failed for witness ${shortPubkey(witness)}`, err))
  },
  healTrustedHostAttestationVerifyFailed(witness: string) {
    console.warn(...fmt('session', `host attestation verify failed for witness ${shortPubkey(witness)}`))
  },
  healTrustedInventoryAttestation(witness: string, covered: number) {
    console.debug(...fmt('session', `witness ${shortPubkey(witness)} has ${covered} events`))
  },
  healTrustedInventoryMissing(witness: string) {
    console.warn(...fmt('session', `witness ${shortPubkey(witness)} missing all events`))
  },
  healTrustedInventoryVerifyFailed(witness: string) {
    console.warn(...fmt('session', `inventory attestation verify failed for witness ${shortPubkey(witness)}`))
  },
  healTrustedNoAttestations() {
    console.warn(...fmt('session', 'trusted heal: no valid attestations collected, falling back'))
  },
  healTrustedBatchResult(url: string, stored: number, rejected: number) {
    console.info(...fmt('session', `trusted heal batch to ${url}: stored=${stored} rejected=${rejected}`))
  },
  healTrustedBatchFailed(url: string, err: unknown) {
    console.warn(...fmt('session', `trusted heal batch failed to ${url}`, err))
  },
  healTrustedComplete(url: string, healed: number, failedCount: number) {
    console.info(...fmt('session', `trusted heal to ${url} complete: healed=${healed} failed=${failedCount}`))
  },
  healTrustedFailed(url: string, err: unknown) {
    console.warn(...fmt('session', `trusted heal failed for ${url}, falling back to slow heal`, err))
  },

  // ── group_status (monitor) ─────────────────────────────────────────
  groupStatusPush(url: string, group: string, setHash: string, count: number, tips: number) {
    console.debug(...fmt('complete', `group_status from ${url}: group=${shortPubkey(group)} set_hash=${shortId(setHash)} count=${count} tips=${tips}`))
  },
  groupStatusVerifyFailed(url: string) {
    console.error(...fmt('complete', `group_status signature/structure verification failed from ${url}`))
  },
  groupStatusDivergence(url: string, localHash: string, remoteHash: string) {
    console.warn(...fmt('complete', `set_hash divergence with ${url}: local=${shortId(localHash)} remote=${shortId(remoteHash)}`))
  },
  groupStatusInSync(url: string) {
    console.debug(...fmt('complete', `in sync with ${url}`))
  },

  // ── event verification / state ─────────────────────────────────────
  eventVerifyStart(type: string, id: string) {
    console.debug(...fmt('event', `verifying ${type} ${shortId(id)}`))
  },
  eventVerifyOk(type: string, id: string) {
    console.debug(...fmt('event', `verified ${type} ${shortId(id)}`))
  },
  eventVerifyFailed(type: string, id: string, reason: string) {
    console.warn(...fmt('event', `verification failed for ${type} ${shortId(id)}: ${reason}`))
  },
  eventSemanticFailed(type: string, id: string, reason: string) {
    console.warn(...fmt('event', `semantic validation failed for ${type} ${shortId(id)}: ${reason}`))
  },

  stateDeriveStart(group: string, eventCount: number) {
    console.debug(...fmt('state', `deriving state for ${shortPubkey(group)} from ${eventCount} events`))
  },
  stateDeriveComplete(group: string, acceptedCount: number, rejectedCount: number) {
    console.debug(...fmt('state', `state derived for ${shortPubkey(group)}: ${acceptedCount} accepted, ${rejectedCount} rejected`))
  },
  stateEventRejected(type: string, id: string, reason: string) {
    console.debug(...fmt('state', `event rejected: ${type} ${shortId(id)} — ${reason}`))
  },
  stateEventApplied(type: string, id: string) {
    console.debug(...fmt('state', `event applied: ${type} ${shortId(id)}`))
  },
  stateChannelCreated(name: string, id: string) {
    console.debug(...fmt('state', `channel created: "${name}" (${shortId(id)})`))
  },
  stateChannelDeleted(name: string, id: string) {
    console.debug(...fmt('state', `channel deleted: "${name}" (${shortId(id)})`))
  },
}

export { shortId, shortPubkey }
