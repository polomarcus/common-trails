"""Tests for app.services.secrets — Fernet at-rest encryption.

Covers:
- Round-trip with key set
- No-op when key is unset (local dev / TEST_MODE path)
- Backwards-compat: plaintext rows decrypt as-is
- Key rotation: ciphertext from a different key falls back to as-is
- Empty string handling
"""
from __future__ import annotations

import importlib

import pytest
from cryptography.fernet import Fernet


@pytest.fixture
def with_key(monkeypatch):
    """Fresh secrets module with a real Fernet key bound."""
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("STRAVA_TOKEN_ENC_KEY", key)
    import app.config as config_mod
    import app.services.secrets as sec_mod
    importlib.reload(config_mod)
    importlib.reload(sec_mod)
    yield sec_mod, key
    monkeypatch.delenv("STRAVA_TOKEN_ENC_KEY", raising=False)
    importlib.reload(config_mod)
    importlib.reload(sec_mod)


@pytest.fixture
def no_key(monkeypatch):
    """Fresh secrets module with no encryption key (local-dev path)."""
    monkeypatch.delenv("STRAVA_TOKEN_ENC_KEY", raising=False)
    import app.config as config_mod
    import app.services.secrets as sec_mod
    importlib.reload(config_mod)
    importlib.reload(sec_mod)
    return sec_mod


def test_round_trip_with_key(with_key):
    sec_mod, _ = with_key
    plaintext = "a7c9f3d8e4b1a0c5d2e6f9a1b3c5d7e9f1a3c5d7"
    encrypted = sec_mod.encrypt_token(plaintext)
    assert encrypted != plaintext
    assert sec_mod.decrypt_token(encrypted) == plaintext


def test_ciphertext_is_different_from_plaintext(with_key):
    sec_mod, _ = with_key
    encrypted = sec_mod.encrypt_token("token_value_123")
    # Fernet output is base64-urlsafe and starts with "gAAAAA"
    assert encrypted.startswith("gAAAAA")
    assert "token_value_123" not in encrypted


def test_no_key_encrypt_is_passthrough(no_key):
    assert no_key.encrypt_token("plaintext_token") == "plaintext_token"


def test_no_key_decrypt_is_passthrough(no_key):
    assert no_key.decrypt_token("plaintext_token") == "plaintext_token"


def test_decrypt_plaintext_with_key_falls_back(with_key):
    """Backwards-compat: rows written before encryption shipped.

    A plaintext token in the DB column is not a valid Fernet message —
    InvalidToken is caught and the raw value is returned so the next
    token refresh can re-encrypt it.
    """
    sec_mod, _ = with_key
    legacy_plaintext = "abc123_legacy_strava_token"
    assert sec_mod.decrypt_token(legacy_plaintext) == legacy_plaintext


def test_decrypt_with_wrong_key_falls_back(monkeypatch):
    """Key rotation: ciphertext from a different key falls back to as-is.

    This is the "don't crash on stale rows during rotation" property —
    not an authentication guarantee. After rotation the next refresh
    rewrites the row under the new key.
    """
    # Encrypt with key A
    key_a = Fernet.generate_key().decode()
    monkeypatch.setenv("STRAVA_TOKEN_ENC_KEY", key_a)
    import app.config as config_mod
    import app.services.secrets as sec_mod
    importlib.reload(config_mod)
    importlib.reload(sec_mod)
    ciphertext_a = sec_mod.encrypt_token("token_value_xyz")
    assert ciphertext_a.startswith("gAAAAA")

    # Switch to key B and try to decrypt — InvalidToken expected
    key_b = Fernet.generate_key().decode()
    monkeypatch.setenv("STRAVA_TOKEN_ENC_KEY", key_b)
    importlib.reload(config_mod)
    importlib.reload(sec_mod)
    result = sec_mod.decrypt_token(ciphertext_a)
    # Falls back to the raw stored value rather than crashing
    assert result == ciphertext_a


def test_empty_string_is_passthrough(with_key):
    sec_mod, _ = with_key
    assert sec_mod.encrypt_token("") == ""
    assert sec_mod.decrypt_token("") == ""


def test_invalid_key_disables_encryption(monkeypatch, caplog):
    """A malformed key falls back gracefully (no startup crash)."""
    monkeypatch.setenv("STRAVA_TOKEN_ENC_KEY", "not_a_valid_fernet_key")
    import app.config as config_mod
    import app.services.secrets as sec_mod
    importlib.reload(config_mod)
    importlib.reload(sec_mod)
    # Encryption is disabled — encrypt is a no-op
    assert sec_mod.encrypt_token("hello") == "hello"
    assert sec_mod.decrypt_token("hello") == "hello"
