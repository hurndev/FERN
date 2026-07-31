"""Pytest configuration for the BFT test suite.

Registers the built-in app modules before any test runs, so genesis/state
fixtures can look up the ``chat`` app module. The protocol core never imports
app code directly; tests (like the CLI and validator entry points) wire the
built-in apps in explicitly.
"""
from __future__ import annotations

from fern.apps import register_builtins

register_builtins()
