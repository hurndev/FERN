from __future__ import annotations

from collections.abc import Iterable, Sequence

from fern.events.event import Event
from fern.events.build import build_event
from fern.identity.user import UserIdentity
from fern.events.types import ChatTypes


def build_nickname_set(
    *,
    user: UserIdentity,
    group: str,
    parents: Sequence[str],
    nickname: str,
    ts: int | None = None,
) -> Event:
    return build_event(
        type=ChatTypes.NICKNAME_SET,
        group=group,
        author_keypair=user.keypair,
        parents=parents,
        content={"nickname": nickname},
        ts=ts,
    )


def resolve_nickname(pubkey: str, events: Iterable[Event]) -> str | None:
    relevant = [e for e in events if e.type == ChatTypes.NICKNAME_SET and e.author == pubkey]
    if not relevant:
        return None
    relevant.sort(key=lambda e: (e.ts, e.id or ""))
    return str(relevant[-1].content["nickname"])
