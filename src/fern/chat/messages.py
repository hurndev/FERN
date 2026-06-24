from __future__ import annotations

from collections.abc import Sequence

from fern.events.event import Event
from fern.events.build import build_event
from fern.identity.user import UserIdentity
from fern.events.types import ChatTypes


def build_chat_message(
    *,
    user: UserIdentity,
    group: str,
    parents: Sequence[str],
    text: str,
    channel: str,
    reply_to: str | None = None,
    ts: int | None = None,
) -> Event:
    content: dict[str, object] = {"text": text, "channel": channel}
    if reply_to is not None:
        content["reply_to"] = reply_to

    return build_event(
        type=ChatTypes.MESSAGE,
        group=group,
        author_keypair=user.keypair,
        parents=parents,
        content=content,
        ts=ts,
    )


def is_chat_message(event: Event) -> bool:
    return event.type == ChatTypes.MESSAGE
