"""Small helpers shared by tests."""

from __future__ import annotations

import os

# Variables a Windows child process needs even when a test wants an otherwise minimal environment.
# Without SYSTEMROOT, `import asyncio` fails with WinError 10106 because Winsock cannot initialize.
_WINDOWS_ESSENTIALS = ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP")


def subprocess_env(**extra: str) -> dict[str, str]:
    """A minimal child-process environment: essentials that exist here, plus `extra`.

    Deliberately does not copy the rest of the parent environment, so tests that prove offline or
    clean-environment behavior stay meaningful (for example `PATH=""`).
    """
    base = {name: os.environ[name] for name in _WINDOWS_ESSENTIALS if name in os.environ}
    return {**base, **extra}
