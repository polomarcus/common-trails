"""Fernet-based encryption helpers for at-rest secrets.

Currently scoped to Strava OAuth tokens stored on
:class:`app.db.models.IntegrationAccount`. Symmetric encryption keyed
by ``STRAVA_TOKEN_ENC_KEY`` (Fernet key, base64-urlsafe 32 bytes) from
Secret Manager in prod.

Local-dev / TEST_MODE: when the key is unset, ``encrypt_token`` and
``decrypt_token`` are no-ops. This keeps the test suite + Docker
Compose path working without any extra setup. The DB column stays
``Text`` either way — Fernet output is base64 ASCII.

Backwards-compat: ``decrypt_token`` swallows ``InvalidToken`` and
returns the stored value as-is. This lets us ship encryption without
a data migration: existing plaintext rows continue to read, and the
next token refresh (Strava rotates every 6 h) writes ciphertext.

Generate a key:
    python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
"""
from __future__ import annotations

import logging

from cryptography.fernet import Fernet, InvalidToken

from app.config import STRAVA_TOKEN_ENC_KEY

log = logging.getLogger(__name__)

_fernet: Fernet | None
if STRAVA_TOKEN_ENC_KEY:
    try:
        _fernet = Fernet(STRAVA_TOKEN_ENC_KEY.encode())
    except Exception as exc:
        log.error("Invalid STRAVA_TOKEN_ENC_KEY (Fernet rejected): %s", exc)
        _fernet = None
else:
    _fernet = None


def encrypt_token(plaintext: str) -> str:
    """Encrypt a token for at-rest storage. No-op when key is unset."""
    if not _fernet or not plaintext:
        return plaintext
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt_token(stored: str) -> str:
    """Decrypt a stored token. Falls back to ``stored`` on InvalidToken.

    The fallback is intentional: rows written before encryption shipped
    are still plaintext, and rows written by a previous key (rotation)
    would otherwise crash every read. Returning the raw value lets the
    next write re-encrypt under the current key.
    """
    if not _fernet or not stored:
        return stored
    try:
        return _fernet.decrypt(stored.encode()).decode()
    except InvalidToken:
        return stored
