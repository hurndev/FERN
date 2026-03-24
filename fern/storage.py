"""FERN Storage utilities."""

import os
from pathlib import Path

DEFAULT_STORAGE = "~/.fern"


def get_storage_path(env_var: str | None = None) -> str:
    """Get the storage path for the current context.

    If FERN_TEST_USER is set, automatically uses /tmp/<FERN_TEST_USER>.
    The env_var parameter allows components to override the env var name
    (e.g., "FERN_WEB_STORAGE" for the web UI).
    """
    test_name = os.environ.get("FERN_TEST_USER")
    if test_name:
        path = f"/tmp/{test_name}"
        print(f"[TEST USER] Using storage at {path}")
        return path

    env_path = os.environ.get(env_var) if env_var else None
    if env_path:
        return env_path

    return os.path.expanduser(DEFAULT_STORAGE)


def test_mode_active() -> bool:
    """Check if running in test mode."""
    return bool(os.environ.get("FERN_TEST_USER"))
