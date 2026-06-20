from __future__ import annotations

import time
from collections.abc import Sequence

from fern.events.event import Event
from fern.events.serialization import sign_event
from fern.crypto.keys import Keypair


def build_event(
    *,
    type: str,
    group: str,
    author_keypair: Keypair,
    parents: Sequence[str] = (),
    content: dict[str, object] | None = None,
    ts: int | None = None,
    tags: Sequence[Sequence[str]] = (),
    group_keypair: Keypair | None = None,
) -> Event:
    if ts is None:
        ts = int(time.time())

    event = Event(
        type=type,
        group=group,
        author=author_keypair.pubkey_hex,
        parents=tuple(parents),
        content=content if content is not None else {},
        ts=ts,
        tags=tuple(tuple(t) for t in tags),
    )

    if type == "genesis":
        if group_keypair is None:
            raise ValueError("group_keypair is required for genesis events")
        return sign_event(event, group_keypair, is_genesis=True)

    return sign_event(event, author_keypair, is_genesis=False)
