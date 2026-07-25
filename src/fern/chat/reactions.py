from __future__ import annotations

from fern.events.event import Event
from fern.events.build import build_event
from fern.identity.user import UserIdentity
from fern.events.types import ChatTypes


def build_reaction(
    *,
    user: UserIdentity,
    group: str,
    seq: int,
    target: str,
    emoji: str,
    ts: int | None = None,
) -> Event:
    return build_event(
        type=ChatTypes.REACTION,
        group=group,
        author_keypair=user.keypair,
        seq=seq,
        content={"target": target, "emoji": emoji},
        ts=ts,
    )
