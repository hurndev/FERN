from __future__ import annotations

from dataclasses import dataclass

from fern.crypto.keys import Keypair


@dataclass(frozen=True)
class GroupKeypair:
    keypair: Keypair

    @property
    def pubkey(self) -> str:
        return self.keypair.pubkey_hex

    @property
    def pubkey_bytes(self) -> bytes:
        return self.keypair.pubkey_bytes

    @classmethod
    def generate(cls) -> GroupKeypair:
        kp = Keypair.generate()
        return cls(keypair=kp)

    def sign(self, message: bytes) -> bytes:
        return self.keypair.sign(message)
