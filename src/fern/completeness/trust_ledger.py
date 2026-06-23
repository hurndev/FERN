from __future__ import annotations

from dataclasses import dataclass, field

from fern.completeness.group_statuses import GroupStatus


@dataclass(frozen=True)
class Fault:
    ts: int
    kind: str
    event_id: str | None = None
    evidence: str = ""


@dataclass
class RelayTrustEntry:
    last_group_status: GroupStatus | None = None
    observed_faults: list[Fault] = field(default_factory=list)


@dataclass
class TrustLedger:
    entries: dict[str, RelayTrustEntry] = field(default_factory=dict)

    def ensure_entry(self, relay_pubkey: str) -> RelayTrustEntry:
        if relay_pubkey not in self.entries:
            self.entries[relay_pubkey] = RelayTrustEntry()
        return self.entries[relay_pubkey]

    def add_fault(self, relay_pubkey: str, fault: Fault) -> None:
        entry = self.ensure_entry(relay_pubkey)
        entry.observed_faults.append(fault)

    def update_group_status(self, relay_pubkey: str, group_status: GroupStatus) -> None:
        entry = self.ensure_entry(relay_pubkey)
        entry.last_group_status = group_status

    def get_faults(self, relay_pubkey: str) -> list[Fault]:
        entry = self.entries.get(relay_pubkey)
        if entry is None:
            return []
        return list(entry.observed_faults)
