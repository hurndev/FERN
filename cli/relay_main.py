"""Compatibility shim for previously installed ``fern-relay`` entry points."""

from cli.validator_main import legacy_main


def main() -> None:
    legacy_main()
