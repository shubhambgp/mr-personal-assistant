"""The one long-lived credential this app stores, and its failure modes.

A Google refresh token outlives every session and grants standing access to a
real person's mailbox. The risks worth testing are not "does encryption work" but
the specific ways an at-rest scheme gets quietly weakened: a reused nonce making
ciphertexts comparable, a tampered row decrypting to something, and a missing key
being treated as "no encryption needed" rather than as an error.
"""

from __future__ import annotations

import base64
import os

import pytest

from app import config
from app.core import crypto


@pytest.fixture
def key(monkeypatch):
    """A real 32-byte key, installed the way the app reads it."""
    value = base64.urlsafe_b64encode(os.urandom(32)).decode()
    monkeypatch.setattr(config.settings, "agenda_encryption_key", value)
    return value


def test_a_sealed_token_round_trips(key):
    token = "1//0gRefreshTokenLookalike-_abcDEF"
    assert crypto.open_sealed(crypto.seal(token)) == token


def test_sealing_the_same_token_twice_produces_different_ciphertext(key):
    """A fresh nonce per call, and this is the test that proves it.

    With a fixed or derived nonce, two reps whose tokens happen to match — or one
    rep reconnecting — would produce identical rows, which leaks that fact to
    anyone reading the table. AES-GCM also loses confidentiality outright on
    nonce reuse, so this is not a tidiness property.
    """
    token = "1//0gSameTokenBothTimes"
    assert crypto.seal(token) != crypto.seal(token)


def test_a_tampered_ciphertext_raises_rather_than_returning_anything(key):
    sealed = crypto.seal("1//0gRealToken")
    raw = bytearray(base64.urlsafe_b64decode(sealed))
    raw[-1] ^= 0x01  # flip one bit of the tag
    tampered = base64.urlsafe_b64encode(bytes(raw)).decode()
    with pytest.raises(ValueError, match="failed authentication"):
        crypto.open_sealed(tampered)


def test_a_token_sealed_under_another_key_does_not_open(key, monkeypatch):
    sealed = crypto.seal("1//0gRealToken")
    monkeypatch.setattr(
        config.settings,
        "agenda_encryption_key",
        base64.urlsafe_b64encode(os.urandom(32)).decode(),
    )
    with pytest.raises(ValueError, match="failed authentication"):
        crypto.open_sealed(sealed)


def test_with_no_key_configured_sealing_fails_loudly(monkeypatch):
    """Never silently store a plaintext credential.

    The tempting shape is `if not key: return plaintext`, which turns a
    misconfiguration into a database full of bare refresh tokens.
    """
    monkeypatch.setattr(config.settings, "agenda_encryption_key", "")
    with pytest.raises(crypto.TokenCryptoUnavailable):
        crypto.seal("1//0gRealToken")


def test_settings_reject_a_key_that_is_not_32_bytes():
    """Caught at import, so it cannot fail on the first rep who connects."""
    from app.config import Settings

    with pytest.raises(ValueError, match="32 bytes"):
        Settings(
            jwt_secret="x" * 40,
            google_client_id="id",
            google_client_secret="secret",
            agenda_encryption_key=base64.urlsafe_b64encode(os.urandom(16)).decode(),
        )


def test_settings_reject_a_half_configured_agenda():
    """A client id with no encryption key would complete the OAuth round trip and
    then fail while storing the token — after the rep had already consented.

    `_env_file=None` is load-bearing: Settings reads backend/.env by default, so
    on a machine where the developer HAS configured Google the missing third
    value was silently supplied from the dotenv and this test passed vacuously
    (worse — it failed, because nothing raised). A validator test must construct
    the exact state it claims to test, not whatever the local .env leaves over.
    """
    from app.config import Settings

    with pytest.raises(ValueError, match="must be set together"):
        Settings(
            _env_file=None,
            jwt_secret="x" * 40,
            google_client_id="id",
            google_client_secret="secret",
        )
