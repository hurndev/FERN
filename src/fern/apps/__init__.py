"""Application module registry.

The protocol core (``fern.bft``) is app-agnostic: it delegates all policy and
application state to an :class:`~fern.bft.app.AppModule`. Each supported app
registers a module here, keyed by its ``app`` name (the value committed in
genesis). The core looks modules up by name and never imports app code directly,
which keeps the dependency direction ``app -> core``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fern.bft.app import AppModule


class AppNotSupportedError(ValueError):
    """A group declared an ``app`` this process has no module for."""


_REGISTRY: dict[str, AppModule] = {}


def register_app(app: AppModule) -> None:
    existing = _REGISTRY.get(app.name)
    if existing is not None and existing is not app:
        raise ValueError(f"app module already registered: {app.name!r}")
    _REGISTRY[app.name] = app


def get_app(name: str) -> AppModule:
    app = _REGISTRY.get(name)
    if app is None:
        raise AppNotSupportedError(f"no app module registered for app={name!r}")
    return app


def is_supported(name: str) -> bool:
    return name in _REGISTRY


def supported_apps() -> frozenset[str]:
    return frozenset(_REGISTRY)


def register_builtins() -> None:
    """Register the built-in app modules.

    Called by the CLI/validator entry points and the test suite. The import is
    deferred so importing this package never pulls in app code (which depends on
    the core), avoiding an import cycle.
    """
    from fern.apps.chat import CHAT_APP

    register_app(CHAT_APP)


def clear_registry() -> None:
    _REGISTRY.clear()


__all__ = [
    "AppNotSupportedError",
    "clear_registry",
    "get_app",
    "is_supported",
    "register_app",
    "register_builtins",
    "supported_apps",
]
