from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fern.bft.constants import PROTOCOL_VERSION


EVENT_FIELDS = frozenset(
    {"protocol", "id", "type", "group", "author", "seq", "content", "ts", "tags", "sig"}
)


def _integer(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"event {field_name} must be an integer")
    return value


@dataclass(frozen=True)
class Event:
    type: str
    group: str
    author: str
    seq: int
    content: dict[str, Any] = field(default_factory=dict)
    ts: int = 0
    tags: tuple[tuple[str, ...], ...] = ()
    protocol: str = PROTOCOL_VERSION
    id: str | None = None
    sig: str | None = None

    @property
    def is_genesis(self) -> bool:
        return self.type == "genesis"

    @property
    def is_state_event(self) -> bool:
        from fern.events.types import is_state_event_type

        return is_state_event_type(self.type)

    def to_dict(self) -> dict[str, object]:
        return {
            "protocol": self.protocol,
            "id": self.id,
            "type": self.type,
            "group": self.group,
            "author": self.author,
            "seq": self.seq,
            "content": self.content,
            "ts": self.ts,
            "tags": [list(tag) for tag in self.tags],
            "sig": self.sig,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> Event:
        fields = set(value)
        if fields != EVENT_FIELDS:
            missing = EVENT_FIELDS - fields
            extra = fields - EVENT_FIELDS
            detail = f"missing {sorted(missing)[0]}" if missing else f"extra {sorted(extra)[0]}"
            raise ValueError(f"event has invalid fields: {detail}")
        raw_content = value.get("content", {})
        raw_tags = value.get("tags", [])
        if not isinstance(raw_content, dict):
            raise ValueError("event content must be an object")
        if not isinstance(raw_tags, list):
            raise ValueError("event tags must be an array")
        tags: list[tuple[str, ...]] = []
        for raw_tag in raw_tags:
            if not isinstance(raw_tag, list) or not all(isinstance(item, str) for item in raw_tag):
                raise ValueError("event tag must be an array of strings")
            tags.append(tuple(raw_tag))
        event_id = value.get("id")
        signature = value.get("sig")
        return cls(
            protocol=str(value.get("protocol", "")),
            type=str(value.get("type", "")),
            group=str(value.get("group", "")),
            author=str(value.get("author", "")),
            seq=_integer(value.get("seq"), "seq"),
            content={str(key): item for key, item in raw_content.items()},
            ts=_integer(value.get("ts"), "ts"),
            tags=tuple(tags),
            id=str(event_id) if event_id is not None else None,
            sig=str(signature) if signature is not None else None,
        )
