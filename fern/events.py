"""Event creation, canonical serialisation, and group state derivation for FERN protocol."""

import json
import time
from typing import Any

from . import crypto


# --- Canonical Serialisation ---


def canonical_serialise(
    event_type: str,
    group: str,
    author: str,
    parents: list[str],
    content: Any,
    ts: int,
) -> bytes:
    """Produce the canonical serialisation for hashing and signing.

    JSON array with fields in fixed order, no whitespace.
    parents sorted lexicographically.
    """
    sorted_parents = sorted(parents)
    arr = [event_type, group, author, sorted_parents, content, ts]
    return json.dumps(arr, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def compute_event_id(canonical: bytes) -> str:
    """Compute event ID as SHA-256 of canonical serialisation."""
    return crypto.sha256(canonical)


def sign_event(canonical: bytes, private_key_hex: str) -> str:
    """Sign canonical serialisation with Ed25519 private key."""
    return crypto.sign(private_key_hex, canonical)


def verify_event_signature(event: dict, signer_pubkey: str) -> bool:
    """Verify an event's signature against a given public key."""
    canonical = canonical_serialise(
        event["type"],
        event["group"],
        event["author"],
        event["parents"],
        event["content"],
        event["ts"],
    )
    return crypto.verify(signer_pubkey, event["sig"], canonical)


def verify_event_id(event: dict) -> bool:
    """Verify that an event's id matches its canonical serialisation hash."""
    canonical = canonical_serialise(
        event["type"],
        event["group"],
        event["author"],
        event["parents"],
        event["content"],
        event["ts"],
    )
    return compute_event_id(canonical) == event["id"]


def verify_event(event: dict) -> tuple[bool, str]:
    """Full event verification. Returns (valid, reason)."""
    # Check required fields
    required = [
        "id",
        "type",
        "group",
        "author",
        "parents",
        "content",
        "ts",
        "sig",
    ]
    for field in required:
        if field not in event:
            return False, f"missing field: {field}"

    # Verify ID
    if not verify_event_id(event):
        return False, "id mismatch"

    # Determine signer pubkey
    if event["type"] == "group_genesis":
        signer = event["group"]
    else:
        signer = event["author"]

    # Verify signature
    if not verify_event_signature(event, signer):
        return False, "invalid signature"

    return True, "ok"


# --- Event Creation Helpers ---


def _build_event(
    event_type: str,
    group_hex: str,
    author_hex: str,
    parents: list[str],
    content: Any,
    signer_private_key: str,
    ts: int | None = None,
) -> dict:
    """Build, sign, and return a complete event dict."""
    if ts is None:
        ts = int(time.time())

    canonical = canonical_serialise(
        event_type, group_hex, author_hex, parents, content, ts
    )
    event_id = compute_event_id(canonical)
    sig = sign_event(canonical, signer_private_key)

    return {
        "id": event_id,
        "type": event_type,
        "group": group_hex,
        "author": author_hex,
        "parents": sorted(parents),
        "content": content,
        "ts": ts,
        "sig": sig,
    }


def create_group_genesis(
    group_privkey: str,
    founder_pubkey: str,
    name: str,
    description: str = "",
    public: bool = True,
    relays: list[str] | None = None,
) -> dict:
    """Create a group_genesis event. Signed with group private key."""
    group_pubkey = crypto.public_key_from_private(group_privkey)
    content = {
        "name": name,
        "description": description,
        "public": public,
        "founder": founder_pubkey,
        "mods": [founder_pubkey],
        "relays": relays or [],
    }
    return _build_event(
        "group_genesis",
        group_pubkey,
        founder_pubkey,
        parents=[],
        content=content,
        signer_private_key=group_privkey,
    )


def create_message(
    group_hex: str,
    author_hex: str,
    author_privkey: str,
    content: str,
    parents: list[str],
) -> dict:
    """Create a message event."""
    return _build_event(
        "message",
        group_hex,
        author_hex,
        parents=parents,
        content=content,
        signer_private_key=author_privkey,
    )


def create_group_invite(
    group_hex: str,
    author_hex: str,
    author_privkey: str,
    invitee: str,
    parents: list[str],
) -> dict:
    """Create a group_invite event."""
    content = {"invitee": invitee, "role": "member"}
    return _build_event(
        "group_invite",
        group_hex,
        author_hex,
        parents=parents,
        content=content,
        signer_private_key=author_privkey,
    )


def create_group_join(
    group_hex: str,
    author_hex: str,
    author_privkey: str,
    parents: list[str],
) -> dict:
    """Create a group_join event. Signed by the user themselves."""
    return _build_event(
        "group_join",
        group_hex,
        author_hex,
        parents=parents,
        content={},
        signer_private_key=author_privkey,
    )


def create_group_leave(
    group_hex: str,
    author_hex: str,
    author_privkey: str,
    parents: list[str],
) -> dict:
    """Create a group_leave event. Signed by the user themselves."""
    return _build_event(
        "group_leave",
        group_hex,
        author_hex,
        parents=parents,
        content={},
        signer_private_key=author_privkey,
    )


def create_group_kick(
    group_hex: str,
    author_hex: str,
    author_privkey: str,
    target: str,
    parents: list[str],
) -> dict:
    """Create a group_kick event."""
    content = {"target": target}
    return _build_event(
        "group_kick",
        group_hex,
        author_hex,
        parents=parents,
        content=content,
        signer_private_key=author_privkey,
    )


def create_mod_add(
    group_hex: str,
    author_hex: str,
    author_privkey: str,
    target: str,
    parents: list[str],
) -> dict:
    """Create a mod_add event."""
    content = {"target": target}
    return _build_event(
        "mod_add",
        group_hex,
        author_hex,
        parents=parents,
        content=content,
        signer_private_key=author_privkey,
    )


def create_mod_remove(
    group_hex: str,
    author_hex: str,
    author_privkey: str,
    target: str,
    parents: list[str],
) -> dict:
    """Create a mod_remove event."""
    content = {"target": target}
    return _build_event(
        "mod_remove",
        group_hex,
        author_hex,
        parents=parents,
        content=content,
        signer_private_key=author_privkey,
    )


def create_relay_update(
    group_hex: str,
    author_hex: str,
    author_privkey: str,
    new_relays: list[str],
    parents: list[str],
) -> dict:
    """Create a relay_update event."""
    content = {"relays": new_relays}
    return _build_event(
        "relay_update",
        group_hex,
        author_hex,
        parents=parents,
        content=content,
        signer_private_key=author_privkey,
    )


def create_group_metadata(
    group_hex: str,
    author_hex: str,
    author_privkey: str,
    name: str | None = None,
    description: str | None = None,
    parents: list[str] | None = None,
) -> dict:
    """Create a group_metadata event."""
    content = {}
    if name is not None:
        content["name"] = name
    if description is not None:
        content["description"] = description
    return _build_event(
        "group_metadata",
        group_hex,
        author_hex,
        parents=parents or [],
        content=content,
        signer_private_key=author_privkey,
    )


# --- Group State Derivation ---


class GroupState:
    """Derives group state by replaying events in DAG order."""

    def __init__(self):
        self.members: set[str] = set()  # invited
        self.joined: set[str] = (
            set()
        )  # currently active (have group_join, no leave/kick)
        self.mods: set[str] = set()
        self.relays: list[str] = []
        self.metadata: dict[str, str] = {}
        self.genesis: dict | None = None
        self.public: bool = True

    def apply(self, events: list[dict]) -> None:
        """Apply a list of events in timestamp order (with conflict resolution)."""
        sorted_events = sorted(events, key=lambda e: (e["ts"], e["id"]))

        for event in sorted_events:
            self._apply_one(event)

    def _apply_one(self, event: dict) -> None:
        """Apply a single event to the state.

        Events are processed in topological order (parents before children),
        so authorization at time of processing is correct.
        """
        etype = event["type"]
        content = event["content"]
        author = event.get("author", "")

        if etype == "group_genesis":
            self.genesis = event
            if isinstance(content, dict):
                founder = content.get("founder", "")
                self.members = set([founder])
                self.joined = set([founder])
                self.mods = set([founder])
                self.relays = list(content.get("relays", []))
                self.public = content.get("public", True)
                self.metadata = {
                    "name": content.get("name", ""),
                    "description": content.get("description", ""),
                }
        elif etype == "group_invite":
            # Mods can invite anyone to a group
            if isinstance(content, dict) and author in self.mods:
                self.members.add(content["invitee"])
        elif etype == "group_join":
            if self.public or event["author"] in self.members:
                self.joined.add(event["author"])
        elif etype == "group_leave":
            self.joined.discard(event["author"])
        elif etype == "group_kick":
            # Mods can kick any member except the founder
            if isinstance(content, dict) and author in self.mods:
                target = content["target"]
                if self.genesis and target == self.genesis["content"].get("founder"):
                    return  # founder cannot be kicked
                self.joined.discard(target)
                self.mods.discard(target)
        elif etype == "mod_add":
            # Mods can promote any member to mod
            if isinstance(content, dict) and author in self.mods:
                self.mods.add(content["target"])
        elif etype == "mod_remove":
            # Mods can demote other mods (but not the last mod)
            if isinstance(content, dict) and author in self.mods:
                target = content["target"]
                if len(self.mods) <= 1:
                    return  # cannot remove the last mod
                self.mods.discard(target)
                self.mods.discard(content["target"])
        elif etype == "relay_update":
            # Mods can update relay list
            if isinstance(content, dict) and author in self.mods:
                self.relays = list(content.get("relays", []))
        elif etype == "group_metadata":
            # Mods can update group metadata
            if isinstance(content, dict) and author in self.mods:
                if "name" in content:
                    self.metadata["name"] = content["name"]
                if "description" in content:
                    self.metadata["description"] = content["description"]

    def is_member(self, pubkey: str) -> bool:
        return pubkey in self.members

    def is_joined(self, pubkey: str) -> bool:
        return pubkey in self.joined

    def is_mod(self, pubkey: str) -> bool:
        return pubkey in self.mods

    def can_post(self, pubkey: str) -> bool:
        """Check if a user can post messages (must be joined)."""
        return pubkey in self.joined

    def is_authorized(self, author: str, event_type: str) -> bool:
        """Check if an author is authorised to publish this event type."""
        if event_type == "group_genesis":
            return True  # Verified against group key separately
        if event_type in ("group_join", "group_leave"):
            return True  # Users can always join/leave for themselves
        return self.is_mod(author)

    def to_dict(self) -> dict:
        return {
            "members": sorted(self.members),
            "joined": sorted(self.joined),
            "mods": sorted(self.mods),
            "relays": self.relays,
            "metadata": self.metadata,
            "public": self.public,
        }
