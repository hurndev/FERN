## 1. Identity & Cryptography

**1.1 Tampered event rejection**
Publish a valid event. Modify a single byte of the `content` field. Verify the client rejects it with a signature mismatch. Repeat for every field individually — `ts`, `author`, `parents`, `group`.

**1.2 Wrong-key signing**
Sign an event with User A's privkey but set `author` to User B's pubkey. Verify relay and client both reject it.

**1.3 Canonical serialisation determinism**
Construct the same event on two different machines. Verify the computed `id` hash is identical. Specifically test events with `parents` in different orders — they should produce the same hash after lexicographic sort.

---

## 2. Genesis & Group Creation

**2.1 Valid genesis accepted**
Create a group, publish genesis to all three relays. Verify all three store it. Verify clients derive correct initial state: founder is in both `members` and `mods`, relay list matches genesis content.

**2.2 Genesis with wrong signature**
Sign the genesis event with a random keypair that is not the group keypair. Verify clients reject it even if the relay stores it.

**2.3 Duplicate genesis rejected**
Attempt to publish a second genesis event for the same group pubkey. Verify clients ignore it — only the first valid genesis is canonical.

**2.4 Group address resolution**
Give a client a group address with three relay hints. Bring one relay down before the client connects. Verify the client successfully bootstraps from the remaining two.

---

## 3. DAG Integrity

**3.1 Linear chain**
Publish 100 messages sequentially, each referencing the previous as parent. Verify a fresh client assembles the full chain correctly from genesis.

**3.2 Concurrent branching**
Have Alice and Bob send messages simultaneously, both referencing the same parent. Verify both messages are accepted. Verify the next message from either user references both as parents, merging the branch. Verify all clients display the same merged history.

**3.3 Deep branch merge**
Create a branch of 10 messages from Alice and a parallel branch of 10 from Bob, both descending from the same root. Have a third user send a message referencing both tips. Verify the merge is handled correctly.

**3.4 Out-of-order delivery**
Send M1, M2, M3 to a relay but deliver them to a fresh client in the order M3, M1, M2. Verify the client correctly assembles the DAG regardless of arrival order.

**3.5 Referencing unknown parents**
Send a client an event whose parent hash does not exist in any relay. Verify the client records the gap, marks it visibly, and continues accepting events that descend from the missing one.

**3.6 Cycle prevention**
Construct an event that references itself as a parent. Verify it is rejected. Construct two events that reference each other. Verify at least one is rejected.

---

## 4. Relay Failure & Recovery

**4.1 Single relay down during publish**
Alice publishes M1 to relays A and B only (C is down). Bring C back up. Verify Bob's client detects C is missing M1, fetches it from A or B, and republishes to C. Verify C reaches identical state to A and B.

**4.2 Relay down for extended period**
Publish 50 messages while relay C is offline. Bring C back up. Verify a client connected to C triggers full healing and C ends up with all 50 messages.

**4.3 All relays briefly unreachable**
Take all three relays offline for 30 seconds while Alice publishes. Verify Alice's client queues the message. When relays come back, verify the message is published successfully.

**4.4 Relay crashes mid-stream**
A fresh client begins syncing history from relay A. After receiving 40% of the history, relay A crashes. Verify the client seamlessly switches to relay B and completes the sync without duplicates or gaps.

**4.5 Two of three relays down**
Only one relay is available. Verify clients can still read and publish. Verify when the other two relays come back, they are healed to match the surviving relay.

**4.6 Relay returns corrupt data**
Configure a relay to return events with modified content (but intact structure). Verify clients detect the invalid signatures and discard the corrupt events. Verify the client then fetches those events from a different relay.

---

## 5. Censorship

**5.1 Relay silently drops a message**
Relay B drops M5 but accepts all others. Verify that Bob, connected to B, can see M5 is missing because M6 references it. Verify Bob's client fetches M5 from relay A and republishes to B.

**5.2 Relay drops all messages from a specific user**
Relay B drops every event where `author = Alice's pubkey`. Verify clients detect the gaps. Verify Alice's events are retrievable from relays A and C.

**5.3 Relay withholds entire history from new joiners**
New client joins and connects only to the censoring relay, which returns no history. Verify the client receives a summary indicating history exists. Verify the client's relay-hint fallback mechanism eventually connects to an honest relay.

**5.4 Relay reorders event delivery**
Relay delivers events in reverse chronological order to confuse a client. Verify the client still assembles the correct DAG regardless of delivery order.

---

## 6. Relay Migration

**6.1 Basic migration**
Mod publishes a `relay_update` replacing relay C with relay D (new relay, empty history). Verify all connected clients detect the update. Verify clients seed relay D with full history. Verify new messages are published to A, B, D and not C.

**6.2 Migration under load**
Publish messages continuously while a migration is in progress. Verify no messages are lost. Verify relay D ends up with complete history including messages sent during migration.

**6.3 Partial migration failure**
Relay D goes offline immediately after being added to the canonical list, before seeding is complete. Verify clients detect D is missing history. Verify when D comes back online it is fully healed.

**6.4 Old relay decommissioned before seeding complete**
Relay C is removed from the canonical list and immediately shut down, but relay D was only 60% seeded. Verify clients detect the gap. Verify if any client has the full local cache, it can complete seeding of D.

**6.5 Full relay replacement over time**
Run a long sequence: start with relays A, B, C. Over multiple migration events replace them all one at a time. Verify a client that joins after all migrations have occurred and connects only to the final relay set receives complete history from genesis.

**6.6 Non-mod attempts migration**
A non-mod user publishes a `relay_update` event. Verify all clients reject it as a semantically invalid event. Verify it is never referenced as a parent and is eventually GC'd by the relay.

---

## 7. Group State & Authorisation

**7.1 Invite-only join**
In a private group, have a user publish a message without an invite event. Verify relays and clients reject it.

**7.2 Kicked user cannot post**
Mod kicks User C. Verify subsequent events from User C's pubkey are ignored by clients. Verify if User C had messages before the kick they remain valid.

**7.3 Mod demoted mid-action**
Mod A is demoted. Verify any mod actions they publish after the demotion are treated as invalid.

**7.4 Conflicting simultaneous mod actions**
Two mods simultaneously promote the same user and kick the same user. Deliver the events to different relays in opposite order. Verify all clients converge to the same state via the conflict resolution rule (lexicographically greater event ID).

**7.5 Founder cannot be kicked**
Attempt to kick the founder. Verify clients reject this.

**7.6 Last mod cannot be removed**
Attempt to remove the last remaining mod. Verify clients reject this, as it would leave the group unmanageable.

---

## 8. Completeness Verification

**8.1 Fresh join with complete history**
Three relays all hold identical history. New client joins. Verify the client confirms completeness after cross-referencing all three.

**8.2 Fresh join with divergent relays**
Relay B is missing 5 events present on A and C. New client joins. Verify the client detects the divergence, identifies the missing events, heals relay B, and then confirms completeness.

**8.3 Permanent gap acknowledged**
A permanent gap exists (event on no relay). Verify the client correctly reports completeness as partial — it knows the gap exists, knows the specific hash, and treats subsequent events as valid.

**8.4 Large history performance**
Seed a group with 10,000 events. Verify a fresh client completes sync and completeness verification within an acceptable time bound.

---

## 9. Garbage Collection

**9.1 Invalid event GC'd**
A non-mod publishes an invalid `group_kick`. Verify it is stored by the relay. Publish N subsequent events (N = your GC threshold). Verify the relay discards the invalid event.

**9.2 Valid tip not GC'd**
Alice sends the most recent message in the group. No further messages are sent. Verify the relay does not GC Alice's message regardless of time elapsed, because it has not been passed by N subsequent events.

**9.3 GC does not break chain**
Verify that after GC runs, the remaining DAG is still fully traversable. No valid events should be removed.

---

## 10. Stress & Edge Cases

**10.1 Thundering herd**
100 users all send messages simultaneously. Verify all messages are eventually stored on all relays. Verify the DAG is consistent across all clients.

**10.2 Long-running group simulation**
Simulate a group running for 6 simulated months: continuous messages, relay migrations, user joins and kicks, occasional relay failures. Verify a client joining at the end can reconstruct the full history correctly.

**10.3 Very old relay hint**
Give a client a group address where the relay hints are three relays that were decommissioned years ago and replaced through several migration events. The hints point nowhere. Verify the client fails gracefully and surfaces a clear error rather than silently providing incomplete history.

**10.4 Identical timestamps**
Publish two events with exactly the same timestamp. Verify conflict resolution via event ID lexicographic ordering is deterministic and consistent across all clients.

**10.5 Extremely large message**
Publish an event with content at the maximum allowed size. Verify it is handled correctly at relay and client level.

**10.6 Network partition and rejoin**
Split the relay set into two partitions for a period: Alice can only reach A, Bob can only reach C, B is isolated. Each side produces messages. When the partition heals, verify all messages are eventually propagated and the DAG on all relays converges to the same state.
