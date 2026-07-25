from __future__ import annotations

from fern.bft.canonical import canonical_json
from fern.events.event import Event


def sort_keys_recursive(obj: object) -> object:
    if isinstance(obj, dict):
        return {k: sort_keys_recursive(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [sort_keys_recursive(i) for i in obj]
    return obj


def _sort_tags(tags: tuple[tuple[str, ...], ...]) -> tuple[tuple[str, ...], ...]:
    return tuple(sorted(tags))


def canonical_serialization(event: Event) -> bytes:
    sorted_tags = _sort_tags(event.tags)
    sorted_content = sort_keys_recursive(event.content)

    array = [
        event.protocol,
        event.type,
        event.group,
        event.author,
        event.seq,
        sorted_content,
        event.ts,
        sorted_tags,
    ]
    return canonical_json(array)


def compute_id(event: Event) -> str:
    from fern.crypto.hashes import sha256_hex

    return sha256_hex(canonical_serialization(event))


def sign_event(event: Event, keypair: object, *, is_genesis: bool = False) -> Event:
    from fern.crypto.keys import Keypair

    assert isinstance(keypair, Keypair)  # type guard
    canon_bytes = canonical_serialization(event)
    event_id = compute_id(event)
    sig_hex = keypair.sign_detached(canon_bytes)
    return Event(
        protocol=event.protocol,
        type=event.type,
        group=event.group,
        author=event.author,
        seq=event.seq,
        content=event.content,
        ts=event.ts,
        tags=event.tags,
        id=event_id,
        sig=sig_hex,
    )
