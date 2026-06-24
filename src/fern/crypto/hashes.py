import hashlib
import secrets


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_bytes(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def random_channel_id() -> str:
    return secrets.token_hex(32)
