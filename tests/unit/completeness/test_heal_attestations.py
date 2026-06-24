from __future__ import annotations

import pytest

from fern.completeness.heal_attestations import (
    HealChallenge,
    Threshold,
    TrustedWitness,
    build_group_host_attestation,
    build_heal_challenge,
    build_inventory_attestation,
    canonical_serialization_group_host_attestation,
    canonical_serialization_heal_challenge,
    canonical_serialization_inventory_attestation,
    compute_challenge_id,
    threshold_required,
    verify_group_host_attestation,
    verify_heal_challenge,
    verify_inventory_attestation,
)
from fern.crypto.keys import Keypair


@pytest.fixture
def keypairs():
    return {
        "receiver": Keypair.from_privkey(b"r" + b"\x00" * 31),
        "w1": Keypair.from_privkey(b"1" + b"\x00" * 31),
        "w2": Keypair.from_privkey(b"2" + b"\x00" * 31),
        "w3": Keypair.from_privkey(b"3" + b"\x00" * 31),
        "attacker": Keypair.from_privkey(b"x" + b"\x00" * 31),
    }


@pytest.fixture
def ids(keypairs):
    return [keypairs["w1"].pubkey_hex, keypairs["w2"].pubkey_hex]


@pytest.fixture
def challenge(keypairs, ids):
    tw = (
        TrustedWitness(relay=keypairs["w1"].pubkey_hex, url="wss://w1/"),
        TrustedWitness(relay=keypairs["w2"].pubkey_hex, url="wss://w2/"),
    )
    return build_heal_challenge(
        group=keypairs["receiver"].pubkey_hex,
        receiver_keypair=keypairs["receiver"],
        ids=ids,
        trusted_witnesses=tw,
        threshold=Threshold(),
        ts=1000,
        expires=2000,
        nonce="a" * 32,
    )


class TestCanonicalSerialization:
    def test_heal_challenge_is_deterministic(self, challenge):
        canon1 = canonical_serialization_heal_challenge(challenge)
        canon2 = canonical_serialization_heal_challenge(challenge)
        assert canon1 == canon2

    def test_heal_challenge_id_stable(self, challenge):
        assert compute_challenge_id(challenge) == compute_challenge_id(challenge)

    def test_heal_challenge_excludes_sig(self, challenge):
        canon = canonical_serialization_heal_challenge(challenge)
        assert b'"sig"' not in canon

    def test_witnesses_sorted_by_pubkey(self, keypairs, ids, challenge):
        canon = canonical_serialization_heal_challenge(challenge)
        sorted_witnesses = sorted(
            challenge.trusted_witnesses, key=lambda w: w.relay
        )
        positions = [canon.find(w.relay.encode()) for w in sorted_witnesses]
        assert positions == sorted(positions)

    def test_threshold_keys_sorted(self, challenge):
        canon = canonical_serialization_heal_challenge(challenge)
        str_repr = canon.decode("utf-8")
        idx_den = str_repr.find('"den"')
        idx_kind = str_repr.find('"kind"')
        idx_min = str_repr.find('"min"')
        idx_num = str_repr.find('"num"')
        assert idx_den < idx_kind < idx_min < idx_num

    def test_group_host_attestation_excludes_sig(self, keypairs, challenge):
        att = build_group_host_attestation(
            group=keypairs["receiver"].pubkey_hex,
            witness_keypair=keypairs["w1"],
            receiver=keypairs["receiver"].pubkey_hex,
            challenge_id=compute_challenge_id(challenge),
            hosts=True,
            ts=1000,
            expires=2000,
        )
        canon = canonical_serialization_group_host_attestation(att)
        assert b'"sig"' not in canon

    def test_inventory_attestation_excludes_sig(self, keypairs, challenge, ids):
        att = build_inventory_attestation(
            group=keypairs["receiver"].pubkey_hex,
            witness_keypair=keypairs["w1"],
            receiver=keypairs["receiver"].pubkey_hex,
            challenge_id=compute_challenge_id(challenge),
            covered_ids=ids,
            ts=1000,
            expires=2000,
        )
        canon = canonical_serialization_inventory_attestation(att)
        assert b'"sig"' not in canon


class TestBuildVerifyRoundTrip:
    def test_heal_challenge_round_trip(self, challenge, keypairs):
        assert verify_heal_challenge(
            challenge, receiver_pubkey=keypairs["receiver"].pubkey_hex, now_ts=1500
        )

    def test_heal_challenge_expired(self, challenge, keypairs):
        assert not verify_heal_challenge(
            challenge, receiver_pubkey=keypairs["receiver"].pubkey_hex, now_ts=2500
        )

    def test_heal_challenge_wrong_receiver(self, challenge, keypairs):
        assert not verify_heal_challenge(
            challenge, receiver_pubkey=keypairs["w1"].pubkey_hex, now_ts=1500
        )

    def test_heal_challenge_tampered_group(self, challenge, keypairs):
        tampered = HealChallenge(
            type=challenge.type,
            group=keypairs["attacker"].pubkey_hex,
            receiver=challenge.receiver,
            ids_hash=challenge.ids_hash,
            count=challenge.count,
            trusted_witnesses=challenge.trusted_witnesses,
            threshold=challenge.threshold,
            nonce=challenge.nonce,
            ts=challenge.ts,
            expires=challenge.expires,
            sig=challenge.sig,
        )
        assert not verify_heal_challenge(
            tampered, receiver_pubkey=keypairs["receiver"].pubkey_hex, now_ts=1500
        )

    def test_group_host_round_trip(self, keypairs, challenge):
        cid = compute_challenge_id(challenge)
        att = build_group_host_attestation(
            group=keypairs["receiver"].pubkey_hex,
            witness_keypair=keypairs["w1"],
            receiver=keypairs["receiver"].pubkey_hex,
            challenge_id=cid,
            hosts=True,
            ts=1000,
            expires=2000,
        )
        assert verify_group_host_attestation(
            att, challenge_id=cid, witness_pubkey=keypairs["w1"].pubkey_hex, now_ts=1500
        )

    def test_group_host_wrong_challenge(self, keypairs, challenge):
        att = build_group_host_attestation(
            group=keypairs["receiver"].pubkey_hex,
            witness_keypair=keypairs["w1"],
            receiver=keypairs["receiver"].pubkey_hex,
            challenge_id=compute_challenge_id(challenge),
            hosts=True,
            ts=1000,
            expires=2000,
        )
        assert not verify_group_host_attestation(
            att, challenge_id="0" * 64, witness_pubkey=keypairs["w1"].pubkey_hex, now_ts=1500
        )

    def test_group_host_wrong_witness(self, keypairs, challenge):
        cid = compute_challenge_id(challenge)
        att = build_group_host_attestation(
            group=keypairs["receiver"].pubkey_hex,
            witness_keypair=keypairs["w1"],
            receiver=keypairs["receiver"].pubkey_hex,
            challenge_id=cid,
            hosts=True,
            ts=1000,
            expires=2000,
        )
        assert not verify_group_host_attestation(
            att, challenge_id=cid, witness_pubkey=keypairs["w2"].pubkey_hex, now_ts=1500
        )

    def test_inventory_round_trip(self, keypairs, challenge, ids):
        cid = compute_challenge_id(challenge)
        att = build_inventory_attestation(
            group=keypairs["receiver"].pubkey_hex,
            witness_keypair=keypairs["w1"],
            receiver=keypairs["receiver"].pubkey_hex,
            challenge_id=cid,
            covered_ids=ids,
            ts=1000,
            expires=2000,
        )
        assert verify_inventory_attestation(
            att,
            challenge_id=cid,
            witness_pubkey=keypairs["w1"].pubkey_hex,
            now_ts=1500,
            covered_ids=ids,
        )

    def test_inventory_wrong_covered_ids(self, keypairs, challenge, ids):
        cid = compute_challenge_id(challenge)
        att = build_inventory_attestation(
            group=keypairs["receiver"].pubkey_hex,
            witness_keypair=keypairs["w1"],
            receiver=keypairs["receiver"].pubkey_hex,
            challenge_id=cid,
            covered_ids=ids,
            ts=1000,
            expires=2000,
        )
        wrong = [keypairs["w2"].pubkey_hex]
        assert not verify_inventory_attestation(
            att,
            challenge_id=cid,
            witness_pubkey=keypairs["w1"].pubkey_hex,
            now_ts=1500,
            covered_ids=wrong,
        )

    def test_inventory_count_mismatch(self, keypairs, challenge, ids):
        cid = compute_challenge_id(challenge)
        att = build_inventory_attestation(
            group=keypairs["receiver"].pubkey_hex,
            witness_keypair=keypairs["w1"],
            receiver=keypairs["receiver"].pubkey_hex,
            challenge_id=cid,
            covered_ids=ids,
            ts=1000,
            expires=2000,
        )
        assert not verify_inventory_attestation(
            att,
            challenge_id=cid,
            witness_pubkey=keypairs["w1"].pubkey_hex,
            now_ts=1500,
            covered_ids=ids[:1],
        )


class TestThresholdRequired:
    @pytest.mark.parametrize(
        "n,expected",
        [(0, 1), (1, 2), (2, 2), (3, 2), (4, 3), (5, 4), (10, 7)],
    )
    def test_default_threshold(self, n, expected):
        assert threshold_required(n, Threshold()) == expected

    def test_custom_threshold(self):
        t = Threshold(kind="ratio", num=3, den=4, min=1)
        assert threshold_required(4, t) == 3
        assert threshold_required(8, t) == 6
        assert threshold_required(1, t) == max(1, 1)

    def test_zero_returns_impossible(self):
        assert threshold_required(0, Threshold()) == 1