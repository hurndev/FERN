"""Local event cache, DAG operations, and storage for FERN protocol."""

import json
from pathlib import Path

from .events import verify_event, GroupState


class EventDAG:
    """Manages a local event DAG for a single group."""

    def __init__(self, group_pubkey: str, storage_dir: str):
        self.group_pubkey = group_pubkey
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.events: dict[str, dict] = {}  # id -> event
        self.children: dict[str, set[str]] = {}  # id -> set of child ids
        self._load()

    @property
    def db_path(self) -> Path:
        return self.storage_dir / f"{self.group_pubkey}.json"

    def _load(self) -> None:
        """Load events from disk."""
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

    def add_event(self, event: dict, skip_verify: bool = False) -> tuple[bool, str]:
        """Add an event to the DAG. Returns (success, reason)."""
        if event["id"] in self.events:
            return False, "duplicate"

        if not skip_verify:
            valid, reason = verify_event(event)
            if not valid:
                return False, f"verification failed: {reason}"

        if event["group"] != self.group_pubkey:
            return False, "group mismatch"

        self.events[event["id"]] = event

        # Update children index
        for parent_id in event.get("parents", []):
            if parent_id not in self.children:
                self.children[parent_id] = set()
            self.children[parent_id].add(event["id"])

        self._save()
        return True, "ok"

    def get_event(self, event_id: str) -> dict | None:
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

    def get_all_events(self) -> list[dict]:
        """Get all events sorted by timestamp then id."""
        return sorted(self.events.values(), key=lambda e: (e["ts"], e["id"]))

    def get_events_since(self, since_ts: int) -> list[dict]:
        """Get all events with ts > since_ts."""
        return sorted(
            [e for e in self.events.values() if e["ts"] > since_ts],
            key=lambda e: (e["ts"], e["id"]),
        )

    def get_state(self) -> GroupState:
        """Derive current group state from the DAG."""
        state = GroupState()
        state.apply(self.get_all_events())
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

    def get_group_dag(self, group_pubkey: str) -> EventDAG:
        return EventDAG(group_pubkey, str(self.groups_dir))

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
