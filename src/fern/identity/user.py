from __future__ import annotations

from dataclasses import dataclass

from fern.crypto.keys import Keypair


@dataclass(frozen=True)
class UserIdentity:
    keypair: Keypair

    @property
    def pubkey(self) -> str:
        return self.keypair.pubkey_hex

    @property
    def pubkey_bytes(self) -> bytes:
        return self.keypair.pubkey_bytes

    @classmethod
    def generate(cls) -> UserIdentity:
        kp = Keypair.generate()
        return cls(keypair=kp)

    @classmethod
    def from_privkey_hex(cls, hex_priv: str) -> UserIdentity:
        privkey = bytes.fromhex(hex_priv)
        kp = Keypair.from_privkey(privkey)
        return cls(keypair=kp)

    def sign(self, message: bytes) -> bytes:
        return self.keypair.sign(message)
