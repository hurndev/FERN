"""FERN-BFT consensus and verification primitives.

The package is layered deliberately: canonical objects and application
execution have no networking dependency, while the validator runtime builds
on those deterministic pieces.
"""

from fern.bft.constants import PROTOCOL_VERSION

__all__ = ["PROTOCOL_VERSION"]
