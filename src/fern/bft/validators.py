from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from fern.bft.constants import MAX_VALIDATORS, STANDARD_BFT_MIN_VALIDATORS
from fern.bft.canonical import strict_int
from fern.crypto.encoding import is_valid_pubkey_hex


@dataclass(frozen=True, order=True)
class Validator:
    pubkey: str
    url: str
    operator: str = ""

    def __post_init__(self) -> None:
        if not is_valid_pubkey_hex(self.pubkey):
            raise ValueError("validator pubkey must be 64-char lowercase hex")
        parsed = urlparse(self.url)
        if parsed.scheme not in {"ws", "wss"} or not parsed.netloc:
            raise ValueError("validator URL must use ws:// or wss://")
        if len(self.url.encode("utf-8")) > 2048:
            raise ValueError("validator URL is too long")
        if len(self.operator.encode("utf-8")) > 200:
            raise ValueError("validator operator label is too long")

    def to_dict(self) -> dict[str, object]:
        return {"pubkey": self.pubkey, "url": self.url, "operator": self.operator}

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> Validator:
        return cls(
            pubkey=str(value.get("pubkey", "")),
            url=str(value.get("url", "")),
            operator=str(value.get("operator", "")),
        )


@dataclass(frozen=True)
class ValidatorSet:
    epoch: int
    validators: tuple[Validator, ...]
    fault_tolerance: int

    def __post_init__(self) -> None:
        if self.epoch < 0:
            raise ValueError("validator epoch cannot be negative")
        if self.fault_tolerance < 0:
            raise ValueError("fault tolerance cannot be negative")
        validator_count = len(self.validators)
        if validator_count == 0:
            raise ValueError("validator set cannot be empty")
        # floor((n-1)/3) yields 0 for fewer than 4 validators, matching the
        # unanimous small-set mode.
        expected_f = (validator_count - 1) // 3
        if self.fault_tolerance != expected_f:
            raise ValueError(
                f"{validator_count} validators require fault_tolerance={expected_f},"
                f" not {self.fault_tolerance}"
            )
        if validator_count > MAX_VALIDATORS:
            raise ValueError("validator set exceeds protocol maximum")
        pubkeys = [validator.pubkey for validator in self.validators]
        if len(pubkeys) != len(set(pubkeys)):
            raise ValueError("validator keys must be unique")
        urls = [validator.url for validator in self.validators]
        if len(urls) != len(set(urls)):
            raise ValueError("validator URLs must be unique")
        if tuple(pubkeys) != tuple(sorted(pubkeys)):
            raise ValueError("validators must be sorted by pubkey")

    @property
    def quorum(self) -> int:
        if self.is_small_unanimous:
            return len(self.validators)
        return (2 * len(self.validators)) // 3 + 1

    @property
    def is_small_unanimous(self) -> bool:
        return len(self.validators) < STANDARD_BFT_MIN_VALIDATORS

    @property
    def propagation_threshold(self) -> int:
        # f+1 receipts guarantee at least one honest validator accepted the
        # event under the derived fault model.
        return len(self.validators) - self.quorum + 1

    @property
    def round_catchup_threshold(self) -> int:
        # Tendermint f+1 rule (n - q + 1): at least one sender is honest
        # under the derived fault model. BFT-NOTES.md records why f+1 rather
        # than n-1: restarted peers must be able to rejoin a survivor that
        # advanced while quorum was unavailable without replaying every
        # round, while a single Byzantine sender cannot force round
        # advancement in standard mode.
        return len(self.validators) - self.quorum + 1

    @property
    def pubkeys(self) -> frozenset[str]:
        return frozenset(v.pubkey for v in self.validators)

    def contains(self, pubkey: str) -> bool:
        return pubkey in self.pubkeys

    def proposer(self, height: int, round: int) -> Validator:
        if height <= 0 or round < 0:
            raise ValueError("invalid consensus position")
        index = (height + round - 1) % len(self.validators)
        return self.validators[index]

    def to_dict(self) -> dict[str, object]:
        return {
            "epoch": self.epoch,
            "fault_tolerance": self.fault_tolerance,
            "validators": [validator.to_dict() for validator in self.validators],
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> ValidatorSet:
        raw = value.get("validators")
        if not isinstance(raw, list):
            raise ValueError("validators must be an array")
        validators = tuple(Validator.from_dict(item) for item in raw if isinstance(item, dict))
        if len(validators) != len(raw):
            raise ValueError("invalid validator entry")
        return cls(
            epoch=strict_int(value.get("epoch"), "validator epoch"),
            validators=validators,
            fault_tolerance=strict_int(value.get("fault_tolerance"), "validator fault_tolerance"),
        )


def make_validator_set(
    validators: list[Validator] | tuple[Validator, ...], *, epoch: int, fault_tolerance: int
) -> ValidatorSet:
    return ValidatorSet(
        epoch=epoch,
        validators=tuple(sorted(validators, key=lambda validator: validator.pubkey)),
        fault_tolerance=fault_tolerance,
    )
