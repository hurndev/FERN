from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature


class Keypair:
    def __init__(self, privkey_bytes: bytes) -> None:
        if len(privkey_bytes) != 32:
            raise ValueError("Private key must be 32 bytes")
        self._privkey = Ed25519PrivateKey.from_private_bytes(privkey_bytes)
        self._pubkey = self._privkey.public_key()

    @classmethod
    def generate(cls) -> Keypair:
        priv = Ed25519PrivateKey.generate()
        keypair = cls.__new__(cls)
        keypair._privkey = priv
        keypair._pubkey = priv.public_key()
        return keypair

    @classmethod
    def from_privkey(cls, privkey: bytes) -> Keypair:
        return cls(privkey)

    @property
    def pubkey_bytes(self) -> bytes:
        return self._pubkey.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    @property
    def pubkey_hex(self) -> str:
        return self.pubkey_bytes.hex()

    @property
    def privkey_bytes(self) -> bytes:
        return self._privkey.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )

    @property
    def privkey_hex(self) -> str:
        return self.privkey_bytes.hex()

    def sign(self, message: bytes) -> bytes:
        return self._privkey.sign(message)

    def sign_detached(self, message: bytes) -> str:
        return self.sign(message).hex()

    def verify(self, pubkey_bytes: bytes, message: bytes, sig: bytes) -> bool:
        try:
            pubkey = Ed25519PublicKey.from_public_bytes(pubkey_bytes)
            pubkey.verify(sig, message)
            return True
        except InvalidSignature:
            return False

    @staticmethod
    def verify_static(pubkey_bytes: bytes, message: bytes, sig: bytes) -> bool:
        try:
            pubkey = Ed25519PublicKey.from_public_bytes(pubkey_bytes)
            pubkey.verify(sig, message)
            return True
        except InvalidSignature:
            return False
