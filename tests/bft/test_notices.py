from __future__ import annotations

import time

import pytest
from dataclasses import replace

from fern.bft.notices import OperatorNotice, sign_operator_notice, verify_operator_notice
from fern.crypto.keys import Keypair


def _notice_fixture() -> tuple[Keypair, OperatorNotice]:
    key = Keypair.generate()
    now = int(time.time())
    notice = sign_operator_notice(
        OperatorNotice(
            validator=key.pubkey_hex,
            text="Down for maintenance Tuesday",
            ts=now,
            expires=now + 3600,
        ),
        key,
    )
    return key, notice


def test_operator_notice_sign_and_verify() -> None:
    _key, notice = _notice_fixture()
    assert verify_operator_notice(notice)


def test_operator_notice_requires_matching_signer() -> None:
    _key, notice = _notice_fixture()
    with pytest.raises(ValueError, match="does not match signing key"):
        sign_operator_notice(notice, Keypair.generate())


def test_operator_notice_tampered_text_rejected() -> None:
    _key, notice = _notice_fixture()
    assert not verify_operator_notice(replace(notice, text="Moved to a new address"))


def test_operator_notice_tampered_expiry_rejected() -> None:
    _key, notice = _notice_fixture()
    assert not verify_operator_notice(replace(notice, expires=notice.expires + 100000))


def test_operator_notice_expired_shape_rejected() -> None:
    _key, notice = _notice_fixture()
    assert not verify_operator_notice(replace(notice, expires=notice.ts))


def test_operator_notice_empty_text_rejected() -> None:
    key = Keypair.generate()
    now = int(time.time())
    with pytest.raises(ValueError, match="notice text must be 1"):
        sign_operator_notice(
            OperatorNotice(validator=key.pubkey_hex, text="", ts=now, expires=now + 60),
            key,
        )
    # Also reject an unsigned notice with empty text at the verifier level.
    assert not verify_operator_notice(
        OperatorNotice(validator=key.pubkey_hex, text="", ts=now, expires=now + 60, sig="aa" * 64)
    )


def test_operator_notice_oversized_text_rejected() -> None:
    key = Keypair.generate()
    now = int(time.time())
    with pytest.raises(ValueError, match="notice text must be 1"):
        sign_operator_notice(
            OperatorNotice(validator=key.pubkey_hex, text="x" * 201, ts=now, expires=now + 60),
            key,
        )
    # Also reject an unsigned oversized notice at the verifier level.
    assert not verify_operator_notice(
        OperatorNotice(validator=key.pubkey_hex, text="x" * 201, ts=now, expires=now + 60, sig="aa" * 64)
    )
