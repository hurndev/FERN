"""FERN Storage utilities."""

import os
from pathlib import Path

DEFAULT_FERN_DIR = "~/.fern"


def resolve_fern_dir(home: str | None = None) -> Path:
    """Resolve the .fern directory path.

    Priority:
    1. --home argument if provided
    2. FERN_TEST_USER env var -> /tmp/<user>/.fern
    3. ~/.fern default
    """
    if home:
        return Path(os.path.expanduser(home)) / ".fern"

    test_name = os.environ.get("FERN_TEST_USER")
    if test_name:
        path = Path(f"/tmp/{test_name}/.fern")
        print(f"[TEST USER] Using storage at {path}")
        return path

    return Path(os.path.expanduser(DEFAULT_FERN_DIR))
