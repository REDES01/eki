# SPDX-License-Identifier: Apache-2.0
"""API keys, kept in the macOS Keychain — never in eki's database or config.

Only keys the user pastes into eki live here: an Anthropic, OpenAI, xAI or
OpenRouter API key. Subscription logins are a different thing and eki never
holds them; Claude Code and Codex keep their own.
"""
from __future__ import annotations

from typing import Optional

SERVICE = "eki"


def _account(provider_key: str) -> str:
    return f"provider:{provider_key}"


def get(provider_key: str) -> Optional[str]:
    try:
        import keyring
        found = keyring.get_password(SERVICE, _account(provider_key))
        if found is None:
            # anything stored before the rename is still under the old service
            found = keyring.get_password("hub", _account(provider_key))
            if found is not None:
                keyring.set_password(SERVICE, _account(provider_key), found)
        return found
    except Exception:                               # noqa: BLE001
        return None


def put(provider_key: str, secret: str) -> None:
    import keyring
    keyring.set_password(SERVICE, _account(provider_key), secret)


def delete(provider_key: str) -> None:
    try:
        import keyring
        keyring.delete_password(SERVICE, _account(provider_key))
    except Exception:                               # noqa: BLE001
        pass                                        # nothing stored is fine
