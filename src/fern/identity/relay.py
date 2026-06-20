from __future__ import annotations

from dataclasses import dataclass

from fern.crypto.keys import Keypair


@dataclass(frozen=True)
class RelayIdentity:
    keypair: Keypair
    url: str
    pubkey_pinned: str | None = None

    @property
    def pubkey(self) -> str:
        return self.keypair.pubkey_hex

    @classmethod
    def generate(cls, url: str) -> RelayIdentity:
        kp = Keypair.generate()
        return cls(keypair=kp, url=url)
