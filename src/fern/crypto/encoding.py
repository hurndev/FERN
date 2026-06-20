import re

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_HEX128_RE = re.compile(r"^[0-9a-f]{128}$")


def to_hex(data: bytes) -> str:
    return data.hex()


def from_hex(s: str) -> bytes:
    try:
        return bytes.fromhex(s)
    except ValueError:
        raise ValueError(f"Invalid hex string: {s[:20]}...")


def is_valid_pubkey_hex(s: str) -> bool:
    return bool(_HEX64_RE.match(s))


def is_valid_event_id_hex(s: str) -> bool:
    return is_valid_pubkey_hex(s)


def is_valid_sig_hex(s: str) -> bool:
    return bool(_HEX128_RE.match(s))
