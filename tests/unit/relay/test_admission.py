from __future__ import annotations

import pytest

from fern.completeness.heal_attestations import (
    Threshold,
    TrustedWitness,
    build_group_host_attestation,
    build_heal_challenge,
    build_inventory_attestation,
    compute_challenge_id,
)
from fern.crypto.keys import Keypair
from fern.relay.admission import (
    REASON_DENOMINATOR_ZERO,
    REASON_INSUFFICIENT,
    REASON_QUOTA_EXCEEDED,
    InventoryEvidence,
    compute_admission,
)


@pytest.fixture
def kps():
    return {
        "rcv": Keypair.from_privkey(b"r" + b"\x00" * 31),
        "w1": Keypair.from_privkey(b"1" + b"\x00" * 31),
        "w2": Keypair.from_privkey(b"2" + b"\x00" * 31),
        "w3": Keypair.from_privkey(b"3" + b"\x00" * 31),
    }


@pytest.fixture
def ids(kps):
    return ["a" * 64, "b" * 64, "c" * 64]


@pytest.fixture
def challenge(kps, ids):
    tw = tuple(
        TrustedWitness(relay=kps[k].pubkey_hex, url=f"wss://{k}/")
        for k in ("w1", "w2", "w3")
    )
    return build_heal_challenge(
        group=kps["rcv"].pubkey_hex,
        receiver_keypair=kps["rcv"],
        ids=ids,
        trusted_witnesses=tw,
        threshold=Threshold(),
        ts=1000,
        expires=2000,
        nonce="a" * 32,
    )


def _make_host(kps, challenge, witness_key, hosts, ts=1000, expires=2000):
    return build_group_host_attestation(
        group=kps["rcv"].pubkey_hex,
        witness_keypair=kps[witness_key],
        receiver=kps["rcv"].pubkey_hex,
        challenge_id=compute_challenge_id(challenge),
        hosts=hosts,
        ts=ts,
        expires=expires,
    )


def _make_inv(kps, challenge, witness_key, covered, ts=1000, expires=2000):
    return build_inventory_attestation(
        group=kps["rcv"].pubkey_hex,
        witness_keypair=kps[witness_key],
        receiver=kps["rcv"].pubkey_hex,
        challenge_id=compute_challenge_id(challenge),
        covered_ids=covered,
        ts=ts,
        expires=expires,
    )


def _ie(att, covered):
    return InventoryEvidence(att, frozenset(covered))


class TestBasicAdmission:
    def test_two_of_three_attest_all_accepted(self, kps, ids, challenge):
        d = compute_admission(
            challenge=challenge,
            event_ids=ids,
            already_have_ids=frozenset(),
            group_host_attestations=[_make_host(kps, challenge, "w1", True), _make_host(kps, challenge, "w2", True)],
            inventory_evidence=[
                _ie(_make_inv(kps, challenge, "w1", ids), ids),
                _ie(_make_inv(kps, challenge, "w2", ids), ids),
            ],
            now_ts=1500,
            remaining_quota=None,
        )
        assert set(d.accepted) == set(ids)
        assert len(d.rejected) == 0
        assert len(d.denominator) == 3

    def test_one_witness_insufficient(self, kps, ids, challenge):
        d = compute_admission(
            challenge=challenge,
            event_ids=ids,
            already_have_ids=frozenset(),
            group_host_attestations=[_make_host(kps, challenge, "w1", True)],
            inventory_evidence=[_ie(_make_inv(kps, challenge, "w1", ids), ids)],
            now_ts=1500,
            remaining_quota=None,
        )
        assert len(d.accepted) == 0
        for _, reason in d.rejected:
            assert reason == REASON_INSUFFICIENT
        assert len(d.denominator) == 3


class TestHostsFalse:
    def test_clean_hosts_false_removed_from_denom(self, kps, ids, challenge):
        d = compute_admission(
            challenge=challenge,
            event_ids=ids,
            already_have_ids=frozenset(),
            group_host_attestations=[
                _make_host(kps, challenge, "w1", True),
                _make_host(kps, challenge, "w2", True),
                _make_host(kps, challenge, "w3", False),
            ],
            inventory_evidence=[
                _ie(_make_inv(kps, challenge, "w1", ids), ids),
                _ie(_make_inv(kps, challenge, "w2", ids), ids),
            ],
            now_ts=1500,
            remaining_quota=None,
        )
        assert set(d.accepted) == set(ids)
        assert len(d.denominator) == 2

    def test_missing_host_attestation_stays_in_denom(self, kps, ids, challenge):
        d = compute_admission(
            challenge=challenge,
            event_ids=ids,
            already_have_ids=frozenset(),
            group_host_attestations=[
                _make_host(kps, challenge, "w1", True),
                _make_host(kps, challenge, "w2", True),
            ],
            inventory_evidence=[
                _ie(_make_inv(kps, challenge, "w1", ids), ids),
                _ie(_make_inv(kps, challenge, "w2", ids), ids),
            ],
            now_ts=1500,
            remaining_quota=None,
        )
        assert len(d.denominator) == 3
        assert set(d.accepted) == set(ids)


class TestTaintedConflicts:
    def test_hosts_false_plus_inventory_tainted(self, kps, ids, challenge):
        d = compute_admission(
            challenge=challenge,
            event_ids=ids,
            already_have_ids=frozenset(),
            group_host_attestations=[
                _make_host(kps, challenge, "w1", True),
                _make_host(kps, challenge, "w2", True),
                _make_host(kps, challenge, "w3", False),
            ],
            inventory_evidence=[
                _ie(_make_inv(kps, challenge, "w1", ids), ids),
                _ie(_make_inv(kps, challenge, "w2", ids), ids),
                _ie(_make_inv(kps, challenge, "w3", ids), ids),
            ],
            now_ts=1500,
            remaining_quota=None,
        )
        assert len(d.denominator) == 3
        assert set(d.accepted) == set(ids)
        assert kps["w3"].pubkey_hex not in d.admitted_by.get(ids[0], ())

    def test_conflicting_host_attestations_tainted(self, kps, ids, challenge):
        ha3_true = _make_host(kps, challenge, "w3", True)
        ha3_false = _make_host(kps, challenge, "w3", False)
        attacker_inv = _make_inv(kps, challenge, "w3", ids)
        d = compute_admission(
            challenge=challenge,
            event_ids=ids,
            already_have_ids=frozenset(),
            group_host_attestations=[
                _make_host(kps, challenge, "w1", True),
                _make_host(kps, challenge, "w2", True),
                ha3_true,
                ha3_false,
            ],
            inventory_evidence=[
                _ie(_make_inv(kps, challenge, "w1", ids), ids),
                _ie(_make_inv(kps, challenge, "w2", ids), ids),
                _ie(attacker_inv, ids),
            ],
            now_ts=1500,
            remaining_quota=None,
        )
        assert len(d.denominator) == 3
        assert kps["w3"].pubkey_hex not in d.admitted_by.get(ids[0], ())


class TestInventoryValidation:
    def test_attestation_from_non_witness_ignored(self, kps, ids, challenge):
        outsider = Keypair.from_privkey(b"z" + b"\x00" * 31)
        outsider_inv = build_inventory_attestation(
            group=kps["rcv"].pubkey_hex,
            witness_keypair=outsider,
            receiver=kps["rcv"].pubkey_hex,
            challenge_id=compute_challenge_id(challenge),
            covered_ids=ids,
            ts=1000,
            expires=2000,
        )
        d = compute_admission(
            challenge=challenge,
            event_ids=ids,
            already_have_ids=frozenset(),
            group_host_attestations=[
                _make_host(kps, challenge, "w1", True),
                _make_host(kps, challenge, "w2", True),
            ],
            inventory_evidence=[
                _ie(_make_inv(kps, challenge, "w1", ids), ids),
                _ie(_make_inv(kps, challenge, "w2", ids), ids),
                _ie(outsider_inv, ids),
            ],
            now_ts=1500,
            remaining_quota=None,
        )
        assert set(d.accepted) == set(ids)
        assert outsider.pubkey_hex not in d.admitted_by.get(ids[0], ())
        assert kps["w1"].pubkey_hex in d.admitted_by[ids[0]]
        assert kps["w2"].pubkey_hex in d.admitted_by[ids[0]]

    def test_wrong_challenge_id_ignored(self, kps, ids, challenge):
        wrong_inv = build_inventory_attestation(
            group=kps["rcv"].pubkey_hex,
            witness_keypair=kps["w1"],
            receiver=kps["rcv"].pubkey_hex,
            challenge_id="0" * 64,
            covered_ids=ids,
            ts=1000,
            expires=2000,
        )
        d = compute_admission(
            challenge=challenge,
            event_ids=ids,
            already_have_ids=frozenset(),
            group_host_attestations=[
                _make_host(kps, challenge, "w1", True),
                _make_host(kps, challenge, "w2", True),
            ],
            inventory_evidence=[
                _ie(_make_inv(kps, challenge, "w1", ids), ids),
                _ie(_make_inv(kps, challenge, "w2", ids), ids),
                _ie(wrong_inv, ids),
            ],
            now_ts=1500,
            remaining_quota=None,
        )
        assert set(d.accepted) == set(ids)
        assert kps["w1"].pubkey_hex in d.admitted_by[ids[0]]

    def test_expired_attestation_ignored(self, kps, ids, challenge):
        d = compute_admission(
            challenge=challenge,
            event_ids=ids,
            already_have_ids=frozenset(),
            group_host_attestations=[_make_host(kps, challenge, "w1", True)],
            inventory_evidence=[_ie(_make_inv(kps, challenge, "w1", ids), ids)],
            now_ts=5000,
            remaining_quota=None,
        )
        assert len(d.accepted) == 0


class TestQuotaAndAlreadyHave:
    def test_already_have_partitioned(self, kps, ids, challenge):
        d = compute_admission(
            challenge=challenge,
            event_ids=ids,
            already_have_ids=frozenset([ids[0]]),
            group_host_attestations=[_make_host(kps, challenge, "w1", True), _make_host(kps, challenge, "w2", True)],
            inventory_evidence=[
                _ie(_make_inv(kps, challenge, "w1", ids), ids),
                _ie(_make_inv(kps, challenge, "w2", ids), ids),
            ],
            now_ts=1500,
            remaining_quota=None,
        )
        assert ids[0] in d.already_have
        assert ids[0] not in d.accepted
        assert ids[1] in d.accepted
        assert ids[2] in d.accepted

    def test_quota_exceeded(self, kps, ids, challenge):
        d = compute_admission(
            challenge=challenge,
            event_ids=ids,
            already_have_ids=frozenset(),
            group_host_attestations=[_make_host(kps, challenge, "w1", True), _make_host(kps, challenge, "w2", True)],
            inventory_evidence=[
                _ie(_make_inv(kps, challenge, "w1", ids), ids),
                _ie(_make_inv(kps, challenge, "w2", ids), ids),
            ],
            now_ts=1500,
            remaining_quota=1,
        )
        assert len(d.accepted) == 1
        quota_rejected = [r for r in d.rejected if r[1] == REASON_QUOTA_EXCEEDED]
        assert len(quota_rejected) == 2


class TestDenominatorZero:
    def test_all_witnesses_hosts_false(self, kps, ids, challenge):
        d = compute_admission(
            challenge=challenge,
            event_ids=ids,
            already_have_ids=frozenset(),
            group_host_attestations=[
                _make_host(kps, challenge, "w1", False),
                _make_host(kps, challenge, "w2", False),
                _make_host(kps, challenge, "w3", False),
            ],
            inventory_evidence=[],
            now_ts=1500,
            remaining_quota=None,
        )
        assert len(d.denominator) == 0
        for _, reason in d.rejected:
            assert reason == REASON_DENOMINATOR_ZERO