from __future__ import annotations

from typing import Any

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Event:
    type: str
    group: str
    author: str
    parents: tuple[str, ...] = ()
    content: dict[str, Any] = field(default_factory=dict)
    ts: int = 0
    tags: tuple[tuple[str, ...], ...] = ()
    id: str | None = None
    sig: str | None = None

    @property
    def is_genesis(self) -> bool:
        return self.type == "genesis"

    @property
    def is_state_event(self) -> bool:
        from fern.events.types import is_state_event_type

        return is_state_event_type(self.type)
