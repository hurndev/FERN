# FERN Sync Process

This document describes the sync-and-heal process in FERN, including the incremental sync optimization.

---

## Overview

The sync process ensures a client's local DAG matches the canonical relay state. It handles:

1. **Relay discovery** - finding the authoritative relay set from group state
2. **Event synchronization** - downloading missing events from relays
3. **Gap healing** - ensuring all relays have identical event history
4. **Incremental optimization** - avoiding full history downloads when possible

---

## Main Sync Flow

```mermaid
flowchart TD
    Start([Client opens group]) --> CheckLocal{Check local DAG}
    
    CheckLocal -->|Has events| Phase0[Phase 0: Check if sync needed]
    CheckLocal -->|Empty DAG| Phase1[Phase 1: Full sync]
    
    Phase0 --> FetchSummaries[Fetch relay summaries]
    FetchSummaries --> CompareSummaries{Compare local vs relay}
    
    CompareSummaries -->|In sync| Skip[SKIP - Already current]
    CompareSummaries -->|Out of sync| Phase1
    
    Skip --> DeriveRelays[Derive canonical relays]
    DeriveRelays --> CheckGaps{Check for gaps?}
    CheckGaps -->|No gaps| Done([Done])
    CheckGaps -->|Has gaps| ReportGaps[Report gaps to user]
    ReportGaps --> Done
    
    Phase1 --> RelayDiscovery[/Sync with relay discovery/]
    RelayDiscovery --> LoopStart{Any relays to sync?}
    
    LoopStart -->|Yes, first round| IncrementalCheck{local_latest_ts > 0?}
    LoopStart -->|Yes, subsequent| FetchEvents
    
    IncrementalCheck -->|Yes| UseSince[Use since=local_latest_ts]
    IncrementalCheck -->|No| UseZero[Use since=0]
    
    UseSince --> FetchEvents
    UseZero --> FetchEvents
    
    FetchEvents --> MergeEvents[Merge new events]
    MergeEvents --> DeriveState[Derive group state]
    DeriveState --> CheckMigration{Relay list changed?}
    
    CheckMigration -->|Yes| NewRelays[Switch to new relays]
    NewRelays --> LoopStart
    CheckMigration -->|No| Phase2[Phase 2: Heal relays]
    
    LoopStart -->|No more relays| Phase2
    
    Phase2 --> HealLoop{For each canonical relay}
    HealLoop -->|Relay missing events| PushEvents[Push missing events]
    HealLoop -->|Relay current| NextRelay[Next relay]
    
    PushEvents --> NextRelay
    NextRelay --> HealLoop
    
    HealLoop -->|All done| Phase3[Phase 3: Finalize storage]
    Phase3 --> Done
```

---

## Phase 0: Sync Skip Check

This phase optimizes by avoiding unnecessary full history downloads when the client is already in sync.

```mermaid
flowchart TD
    Start[Phase 0: Check if sync needed] --> CheckLocal{Local DAG\nhas events?}
    
    CheckLocal -->|No| SkipCheck[Skip check,\ndo full sync]
    CheckLocal -->|Yes| GetLocalState[Get local state:\n- count\n- latest_ts\n- tips]
    
    GetLocalState --> FetchSummaries[Fetch summary\nfrom all relays]
    FetchSummaries --> CheckResponses{Any relays\nresponded?}
    
    CheckResponses -->|No| SkipCheck
    CheckResponses -->|Yes| CheckAgreement{All relays\nagree?}
    
    CheckAgreement -->|No| SkipCheck
    CheckAgreement -->|Yes| CompareCounts{count matches\nlocal count?}
    
    CompareCounts -->|No| SkipCheck
    CompareCounts -->|Yes| CompareTips{tips match\nlocal tips?}
    
    CompareTips -->|Yes| InSync[SKIP SYNC\nAlready current]
    CompareTips -->|No| SkipCheck
    
    InSync --> DeriveRelays[Derive canonical relays]
    DeriveRelays --> CheckGaps2{Check gaps?}
    CheckGaps2 --> Done0([Return: skipped=true])
    
    SkipCheck --> Continue([Continue to Phase 1])
```

---

## Phase 1: Sync with Relay Discovery

The sync loop handles relay migration by discovering canonical relays through the DAG itself.

```mermaid
flowchart TD
    Start[Phase 1: Sync Loop] --> Init[Init:\n- current_relays = hint_relays\n- seen_relays = {}\n- all_validated = {}]
    
    Init --> LoopTop{current_relays\nis empty?}
    
    LoopTop -->|No| RoundStart[Start sync round]
    LoopTop -->|Yes| Done1([Continue to Phase 2])
    
    RoundStart --> TrackUsed[Track relays used\nthis round]
    TrackUsed --> SyncMode{Local events\nexist?}
    
    SyncMode -->|Yes| SetSince[since = local_latest_ts]
    SyncMode -->|No| SetFull[since = 0]
    
    SetSince --> FetchEvents
    SetFull --> FetchEvents
    
    FetchEvents[Fetch & validate\nevents from relays] --> Merge[Merge into\nall_validated]
    Merge --> CheckGood{Any good\nrelays?}
    
    CheckGood -->|No| NoRelays[No working relays]
    NoRelays --> Done1
    
    CheckGood -->|Yes| TempStore[Store events in DAG\ntemp to derive state]
    TempStore --> DeriveState[Derive group state\nget canonical relays]
    DeriveState --> CompareRelays{derived == used\nthis round?}
    
    CompareRelays -->|Yes| Stable[Relay list stable]
    Stable --> Done1
    
    CompareRelays -->|No| CheckMigration{New relays\nin derived?}
    
    CheckMigration -->|Yes| SwitchRelays[Switch to\ncanonical relays]
    SwitchRelays --> ClearTemp[Clear temp DAG\nstorage]
    ClearTemp --> LoopTop
    
    CheckMigration -->|No| Stable2[Already synced\nall relays]
    Stable2 --> Done1
```

---

## Phase 2: Healing

Healing ensures all canonical relays have identical event history. For **full sync**, all events are compared. For **incremental sync**, only the new events received during this sync are healed (to avoid incorrectly flagging old events as missing).

```mermaid
flowchart TD
    Start[Phase 2: Healing] --> MergeLocal[Merge local\nevents into all_validated]
    
    MergeLocal --> GetCanonical[Get canonical\nrelay list]
    GetCanonical --> ComputeNew[Compute new_event_ids\n= all - event_ids_before_sync]
    
    ComputeNew --> FetchIDs[Fetch event IDs\nfrom all relays]
    
    FetchIDs --> CheckMode{Sync mode?}
    
    CheckMode -->|Full sync| UseAll[events_to_heal = all_validated]
    CheckMode -->|Incremental| UseNew[events_to_heal = new_event_ids]
    
    UseAll --> CheckMissing{Missing events\non any relay?}
    UseNew --> CheckMissing
    
    CheckMissing -->|Yes| PushLoop[Push missing events]
    PushLoop --> NextRelay[Next relay]
    NextRelay --> CheckMissing
    
    CheckMissing -->|No| Done2([Continue to Phase 3])
```

---

## Healing Detail

```mermaid
flowchart LR
    subgraph RelayA[Relay A]
        A1[Event 1]
        A2[Event 2]
        A3[Event 3]
    end
    
    subgraph RelayB[Relay B]
        B1[Event 1]
        B2[Event 2]
        B3MISSING[Event 3?]
    end
    
    subgraph Client[Client all_validated]
        C1[Event 1]
        C2[Event 2]
        C3[Event 3]
    end
    
    C3 -.->|push| B3MISSING
    
    style B3MISSING fill:#ff6b6b
```

---

## Phase 3: Finalize Storage

```mermaid
flowchart TD
    Start[Phase 3: Finalize] --> ClearDAG[Clear local DAG\nin memory]
    ClearDAG --> RebuildIndex[Rebuild children index]
    RebuildIndex --> ReStore[Re-store all events\nin sorted order]
    ReStore --> CheckGaps3{Check for gaps?}
    
    CheckGaps3 -->|No gaps| Complete[DAG complete]
    CheckGaps3 -->|Has gaps| WarnGaps[Report gaps]
    WarnGaps --> Done3([Return summary])
    
    Complete --> Done3
```

---

## Incremental vs Full Sync

```mermaid
flowchart TD
    subgraph Full_Sync[Full Sync\nsince=0]
        F1[Fetch genesis\nfrom all relays]
        F2[Validate genesis\nsignature]
        F3[Fetch ALL events\nsince=0]
        F4[Validate ALL\nevent signatures]
    end
    
    subgraph Incremental_Sync[Incremental Sync\nsince=local_latest_ts]
        I1[Trust local genesis\nSkip genesis fetch]
        I2[Fetch events where\nts >= local_latest_ts]
        I3[Validate new\nevent signatures]
    end
    
    Full_Sync --> |10000 events| Time1[~10 seconds]
    Incremental_Sync --> |5 events| Time2[~100ms]
    
    style Time1 fill:#ff6b6b,color:#fff
    style Time2 fill:#51cf66,color:#fff
```

---

## Summary Response

The `sync_and_heal` function returns a summary dict:

```python
summary = {
    "hint_relays": ["ws://relay1:8787", ...],  # Relays initially contacted
    "canonical_relays": ["ws://relay1:8787", ...],  # Final authoritative relays
    "bad_relays": ["ws://relay2:8787", ...],  # Relays that failed/changed genesis
    "sync_rounds": 2,  # Number of relay discovery rounds
    "total_events": 1005,  # Total events after sync
    "invalid_events": 0,  # Events rejected during validation
    "healed_events": 3,  # Events pushed to lagging relays
    "gaps": [],  # Missing parent event IDs
    "skipped": False,  # True if sync was skipped (already in sync)
}
```

---

## Key Optimization: Summary Check

```mermaid
sequenceDiagram
    participant Client
    participant Relay1
    participant Relay2
    
    Client->>Relay1: summary action
    Client->>Relay2: summary action
    Relay1-->>Client: {count: 1005, tips: [a, b, c]}
    Relay2-->>Client: {count: 1005, tips: [a, b, c]}
    
    Note over Client: Local: count=1005, tips=[a,b,c]
    Note over Client: Relays match local = SKIP!
    
    Client->>Client: Derive canonical relays<br/>Check for gaps<br/>Done!
```
