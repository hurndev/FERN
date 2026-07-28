"""Signed operator notices served out-of-band to connecting clients.

An operator notice is a short message-of-the-day from a validator operator
("maintenance Tuesday", "shutting down in two weeks"). It is deliberately
*not* a consensus object: it never appears in a block, is never validated
against group state, and expires. Clients display it only while it is
current and only when its signature verifies under the validator's known
key.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from fern.bft.canonical import sign_payload, strict_int, verify_payload_signature
from fern.bft.constants import MAX_NOTICE_BYTES
from fern.crypto.encoding import is_valid_pubkey_hex, is_valid_sig_hex
from fern.crypto.keys import Keypair


@dataclass(frozen=True)
class OperatorNotice:
    validator: str
    text: str
    ts: int
    expires: int
    sig: str = ""

    def signing_payload(self) -> list[object]:
        return ["operator_notice", self.validator, self.text, self.ts, self.expires]

    def to_dict(self) -> dict[str, object]:
        return {
            "type": "operator_notice",
            "validator": self.validator,
            "text": self.text,
            "ts": self.ts,
            "expires": self.expires,
            "sig": self.sig,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> OperatorNotice:
        return cls(
            validator=str(value.get("validator", "")),
            text=str(value.get("text", "")),
            ts=strict_int(value.get("ts"), "notice ts"),
            expires=strict_int(value.get("expires"), "notice expires"),
            sig=str(value.get("sig", "")),
        )


def sign_operator_notice(notice: OperatorNotice, keypair: Keypair) -> OperatorNotice:
    if notice.validator != keypair.pubkey_hex:
        raise ValueError("notice validator does not match signing key")
    text_bytes = notice.text.encode("utf-8")
    if len(text_bytes) == 0 or len(text_bytes) > MAX_NOTICE_BYTES:
        raise ValueError(
            f"notice text must be 1-{MAX_NOTICE_BYTES} bytes, got {len(text_bytes)}"
        )
    return replace(notice, sig=sign_payload(keypair, notice.signing_payload()))


def verify_operator_notice(notice: OperatorNotice) -> bool:
    return (
        is_valid_pubkey_hex(notice.validator)
        and 0 < len(notice.text.encode("utf-8")) <= MAX_NOTICE_BYTES
        and notice.ts >= 0
        and notice.expires > notice.ts
        and is_valid_sig_hex(notice.sig)
        and verify_payload_signature(notice.validator, notice.signing_payload(), notice.sig)
    )


__all__ = [
    "OperatorNotice",
    "sign_operator_notice",
    "verify_operator_notice",
]
