from __future__ import annotations

from collections.abc import Sequence


from fern.crypto.keys import Keypair


def build_metadata(
    *,
    relay_keypair: Keypair,
    name: str,
    description: str = "",
    groups: Sequence[str] | None = None,
    retention: str = "full",
) -> dict[str, object]:
    return {
        "name": name,
        "description": description,
        "pubkey": relay_keypair.pubkey_hex,
        "software": "fern-relay-python",
        "version": "0.1.0",
        "groups": list(groups) if groups is not None else [],
        "retention": {"default": retention},
    }
