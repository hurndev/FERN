from __future__ import annotations

import pytest

from fern.crypto.keys import Keypair
from fern.identity.user import UserIdentity
from fern.identity.group import GroupKeypair
from fern.events.event import Event
from fern.events.build import build_event
from fern.events.types import ProtocolTypes
from fern.storage.memory import MemoryStore


@pytest.fixture
def alice_keypair() -> Keypair:
    return Keypair.from_privkey(b"alice" + b"\x00" * 27)


@pytest.fixture
def bob_keypair() -> Keypair:
    return Keypair.from_privkey(b"bob!!" + b"\x00" * 27)


@pytest.fixture
def founder_identity() -> UserIdentity:
    kp = Keypair.from_privkey(b"found" + b"\x00" * 27)
    return UserIdentity(keypair=kp)


@pytest.fixture
def alice_identity() -> UserIdentity:
    kp = Keypair.from_privkey(b"alice" + b"\x00" * 27)
    return UserIdentity(keypair=kp)


@pytest.fixture
def bob_identity() -> UserIdentity:
    kp = Keypair.from_privkey(b"bob!!" + b"\x00" * 27)
    return UserIdentity(keypair=kp)


@pytest.fixture
def group_keypair() -> GroupKeypair:
    kp = Keypair.from_privkey(b"group" + b"\x00" * 27)
    return GroupKeypair(keypair=kp)


@pytest.fixture
def sample_genesis(founder_identity: UserIdentity, group_keypair: GroupKeypair) -> Event:
    return build_event(
        type=ProtocolTypes.GENESIS,
        group=group_keypair.pubkey,
        author_keypair=founder_identity.keypair,
        parents=(),
        content={
            "name": "Test Group",
            "description": "A group for testing",
            "public": True,
            "founder": founder_identity.pubkey,
            "admins": [founder_identity.pubkey],
            "relays": ["wss://relay1.test", "wss://relay2.test"],
            "app": "chat",
            "chat.channels": [{"id": "general", "name": "general", "position": 0}],
            "chat.default_channel": "general",
            "chat.system_channel": "general",
        },
        group_keypair=group_keypair.keypair,
    )


@pytest.fixture
def memory_store() -> MemoryStore:
    return MemoryStore()
