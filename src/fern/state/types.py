from __future__ import annotations

from collections.abc import Mapping

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BanEntry:
    until: int | None
    reason: str


@dataclass(frozen=True)
class Channel:
    id: str
    name: str
    description: str = ""
    position: int = 0


@dataclass(frozen=True)
class GroupState:
    members: frozenset[str]
    joined: frozenset[str]
    banned: Mapping[str, BanEntry]
    admins: frozenset[str]
    relays: tuple[str, ...]
    metadata: Mapping[str, str]
    public: bool
    app: str = "chat"
    channels: Mapping[str, Channel] = field(
        default_factory=dict
    )
    chat_settings: Mapping[str, str] = field(
        default_factory=dict
    )

    def is_banned_at(self, pubkey: str, ts: int) -> bool:
        entry = self.banned.get(pubkey)
        if entry is None:
            return False
        if entry.until is None:
            return True
        return entry.until > ts

    def can_post(self, pubkey: str, ts: int) -> bool:
        return pubkey in self.joined and not self.is_banned_at(pubkey, ts)

    def can_admin(self, pubkey: str) -> bool:
        return pubkey in self.admins
