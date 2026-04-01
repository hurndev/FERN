"""Local event cache, DAG operations, and storage for FERN protocol."""

import json
from pathlib import Path

from .events import Event, verify_event, verify_event_authorization, GroupState


class EventDAG:
    """Manages a local event DAG for a single group."""

    def __init__(self, group_pubkey: str, storage_dir: str):
        self.group_pubkey = group_pubkey
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.events: dict[str, Event] = {}  # id -> event
        self.children: dict[str, set[str]] = {}  # id -> set of child ids
        self._state_cache: GroupState | None = None
        self._state_event_count: int = 0
        self._load()

    @property
    def db_path(self) -> Path:
        return self.storage_dir / f"{self.group_pubkey}.json"

    def _load(self) -> None:
        """Load events from disk."""
        self._invalidate_state_cache()
        if self.db_path.exists():
            with open(self.db_path, "r") as f:
                data = json.load(f)
            for event in data.get("events", []):
                self.events[event["id"]] = event
            self._rebuild_children()

    def _save(self) -> None:
        """Persist events to disk."""
        with open(self.db_path, "w") as f:
            json.dump({"events": list(self.events.values())}, f, indent=2)

    def _rebuild_children(self) -> None:
        """Rebuild the children index from events."""
        self.children = {}
        for eid, event in self.events.items():
            for parent_id in event.get("parents", []):
                if parent_id not in self.children:
                    self.children[parent_id] = set()
                self.children[parent_id].add(eid)

    def _invalidate_state_cache(self) -> None:
        """Invalidate the cached GroupState."""
        self._state_cache = None
        self._state_event_count = 0

    def add_event(
        self,
        event: Event,
        skip_verify: bool = False,
        skip_save: bool = False,
        skip_auth: bool = False,
    ) -> tuple[bool, str]:
        """Add an event to the DAG. Returns (success, reason)."""
        if event["id"] in self.events:
            return False, "duplicate"

        if not skip_verify:
            valid, reason = verify_event(event)
            if not valid:
                return False, f"verification failed: {reason}"

        if event["group"] != self.group_pubkey:
            return False, "group mismatch"

        if not skip_auth:
            state = self.get_state()
            authorized, reason = verify_event_authorization(event, state)
            if not authorized:
                return False, f"authorization failed: {reason}"

        self.events[event["id"]] = event

        # Update children index
        for parent_id in event.get("parents", []):
            if parent_id not in self.children:
                self.children[parent_id] = set()
            self.children[parent_id].add(event["id"])

        # Incrementally update cached state
        if self._state_cache is not None:
            self._state_cache._apply_one(event)
            self._state_event_count = len(self.events)

        if not skip_save:
            self._save()
        return True, "ok"

    def get_event(self, event_id: str) -> Event | None:
        return self.events.get(event_id)

    def get_tips(self) -> list[str]:
        """Get event IDs that have no children (frontier of the DAG)."""
        all_parents = set()
        for event in self.events.values():
            all_parents.update(event.get("parents", []))
        tips = set(self.events.keys()) - all_parents
        return sorted(tips)

    def get_missing_parents(self) -> set[str]:
        """Get parent IDs referenced but not present in the DAG."""
        referenced = set()
        for event in self.events.values():
            referenced.update(event.get("parents", []))
        return referenced - set(self.events.keys())

    def get_all_events(self) -> list[Event]:
        """Get all events sorted by timestamp then id."""
        return sorted(self.events.values(), key=lambda e: (e["ts"], e["id"]))

    def get_events_since(self, since_ts: int) -> list[Event]:
        """Get all events with ts > since_ts."""
        return sorted(
            [e for e in self.events.values() if e["ts"] > since_ts],
            key=lambda e: (e["ts"], e["id"]),
        )

    def get_state(self) -> GroupState:
        """Derive current group state from the DAG, using cache when valid."""
        if self._state_cache is not None and self._state_event_count == len(
            self.events
        ):
            return self._state_cache
        state = GroupState()
        state.apply(self.get_all_events())
        self._state_cache = state
        self._state_event_count = len(self.events)
        return state

    @property
    def count(self) -> int:
        return len(self.events)


class ClientStorage:
    """Manages local storage for multiple groups and user identity."""

    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.groups_dir = self.base_dir / "groups"
        self.groups_dir.mkdir(exist_ok=True)
        self.keys_dir = self.base_dir / "keys"
        self.keys_dir.mkdir(exist_ok=True)
        self._dag_cache: dict[str, EventDAG] = {}

    def get_group_dag(self, group_pubkey: str) -> EventDAG:
        if group_pubkey not in self._dag_cache:
            self._dag_cache[group_pubkey] = EventDAG(group_pubkey, str(self.groups_dir))
        return self._dag_cache[group_pubkey]

    def list_groups(self) -> list[str]:
        """List all group pubkeys we have local data for."""
        groups = []
        for f in self.groups_dir.glob("*.json"):
            groups.append(f.stem)
        return sorted(groups)

    def get_user_key_path(self) -> str:
        return str(self.keys_dir / "user.pem")

    def get_group_key_path(self, name: str = "default") -> str:
        return str(self.keys_dir / f"group_{name}.pem")

    def save_group_key(self, group_privkey: str, name: str) -> None:
        """Save a group private key."""
        from .crypto import save_keypair

        path = self.get_group_key_path(name)
        save_keypair(group_privkey, path)
