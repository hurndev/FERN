"""Ed25519 and SHA-256 cryptographic primitives for FERN protocol."""

import hashlib
import json
import os

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    PrivateFormat,
    NoEncryption,
    BestAvailableEncryption,
    load_pem_private_key,
    load_pem_public_key,
)


def generate_keypair() -> tuple[str, str]:
    """Generate a new Ed25519 keypair. Returns (private_hex, public_hex)."""
    privkey = Ed25519PrivateKey.generate()
    priv_bytes = privkey.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    pub_bytes = privkey.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return priv_bytes.hex(), pub_bytes.hex()


def sha256(data: bytes) -> str:
    """Compute SHA-256 hash, returned as lowercase hex."""
    return hashlib.sha256(data).hexdigest()


def sign(private_key_hex: str, message: bytes) -> str:
    """Sign a message with an Ed25519 private key. Returns signature hex."""
    privkey = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    sig = privkey.sign(message)
    return sig.hex()


def verify(public_key_hex: str, signature_hex: str, message: bytes) -> bool:
    """Verify an Ed25519 signature. Returns True if valid."""
    try:
        pubkey = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        pubkey.verify(bytes.fromhex(signature_hex), message)
        return True
    except Exception:
        return False


def save_keypair(private_key_hex: str, path: str, password: str | None = None) -> None:
    """Save a private key to a PEM file (optionally encrypted)."""
    privkey = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    encryption = (
        BestAvailableEncryption(password.encode()) if password else NoEncryption()
    )
    pem = privkey.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, encryption)
    with open(path, "wb") as f:
        f.write(pem)
    os.chmod(path, 0o600)


def load_private_key(path: str, password: str | None = None) -> str:
    """Load a private key from a PEM file. Returns hex string."""
    with open(path, "rb") as f:
        pem = f.read()
    return load_private_key_from_pem(pem, password)


def load_private_key_from_pem(pem: bytes, password: str | None = None) -> str:
    """Load a private key from PEM bytes. Returns hex string."""
    privkey = load_pem_private_key(pem, password.encode() if password else None)
    assert isinstance(privkey, Ed25519PrivateKey)
    raw = privkey.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    return raw.hex()


def public_key_from_private(private_key_hex: str) -> str:
    """Derive public key from private key hex."""
    privkey = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_key_hex))
    pub_bytes = privkey.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return pub_bytes.hex()
