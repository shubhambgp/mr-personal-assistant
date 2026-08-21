"""Authenticated encryption for the one long-lived credential this app stores.

A Google refresh token is not like the other secrets here. The JWT secret and the
OpenAI key are the operator's, live only in the environment, and are the same for
every rep. A refresh token belongs to a *person*, grants standing access to their
mailbox until revoked, and there is one per rep sitting in a database row.

So it is encrypted at rest with AES-256-GCM, keyed by AGENDA_ENCRYPTION_KEY.
That does not defend against an attacker who has both the database and the
application's environment — nothing at this layer can. What it does defend
against is the realistic failure: a database backup, a replica, a `pg_dump` in a
ticket, or a stray SELECT by someone with database access but not deploy access.

AES-256-GCM rather than Fernet: Fernet is CBC+HMAC wrapped around a timestamp we
do not want, and it would be the only reason to reach for another abstraction.
This is one primitive and a dozen lines.
"""

from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..config import settings

#: 96 bits, the size AES-GCM is specified and optimised for.
_NONCE_BYTES = 12

#: Bumped only when the key changes. Stored on the row so a rotation can re-wrap
#: without guessing which key produced which ciphertext.
KEY_VERSION = 1


class TokenCryptoUnavailable(RuntimeError):
    """Raised when a seal/open is attempted with no key configured."""


def _key() -> bytes:
    if not settings.agenda_encryption_key:
        raise TokenCryptoUnavailable(
            "AGENDA_ENCRYPTION_KEY is not set, so a Google refresh token cannot be "
            "stored or read. Generate one with: python -c "
            '"import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"'
        )
    # Length is validated at import by Settings._agenda_is_all_or_nothing, so a
    # bad key fails at startup rather than on the first rep who connects.
    return base64.urlsafe_b64decode(settings.agenda_encryption_key)


def seal(plaintext: str) -> str:
    """Encrypt. Returns base64url(nonce || ciphertext-with-tag).

    A FRESH nonce every call, from os.urandom. GCM loses confidentiality
    catastrophically on nonce reuse — not gracefully, and not only for the reused
    message — so the nonce is never derived from the chair_id, the rep_code, a
    counter, or anything else that could repeat.
    """
    nonce = os.urandom(_NONCE_BYTES)
    sealed = AESGCM(_key()).encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.urlsafe_b64encode(nonce + sealed).decode("ascii")


def open_sealed(blob: str) -> str:
    """Decrypt, or raise.

    Deliberately raises rather than returning None on a bad tag. A tampered or
    truncated token is not a cache miss to be papered over — it means the row or
    the key is wrong, and the only safe response is to stop and make the rep
    reconnect.
    """
    raw = base64.urlsafe_b64decode(blob.encode("ascii"))
    nonce, sealed = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
    try:
        return AESGCM(_key()).decrypt(nonce, sealed, None).decode("utf-8")
    except InvalidTag as exc:
        raise ValueError(
            "stored credential failed authentication — wrong key, or the row was altered"
        ) from exc
