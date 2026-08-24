"""Settings, from the environment. One place, validated at import.

Anything secret (DB password, JWT secret, OpenAI key, the Google client secret
and the agenda encryption key) is read here and nowhere else. There are no defaults for secrets — a missing JWT secret must fail loudly
at startup rather than silently fall back to something guessable.
"""

from __future__ import annotations

import base64
import binascii
import os
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _system_timezone() -> str:
    """The host's IANA zone name, so `AGENDA_TIMEZONE` need not be configured.

    There is no stdlib call for this: `zoneinfo` resolves a name to a zone but
    will not tell you which name is local, and `datetime.astimezone().tzname()`
    returns an abbreviation ("IST") that `ZoneInfo` cannot resolve back. Reading
    the `/etc/localtime` symlink is the portable-on-Linux way, and `TZ` wins when
    set because that is how a container is told where it lives.

    Falls back to UTC rather than guessing. A wrong zone makes tasks flip to
    overdue a few hours early — quiet wrongness, so the fallback is the one zone
    that is never a silent lie about somewhere else.
    """
    from_env = os.environ.get("TZ", "").strip()
    if from_env:
        return from_env
    try:
        link = Path("/etc/localtime")
        if link.is_symlink():
            parts = link.resolve().parts
            if "zoneinfo" in parts:
                return "/".join(parts[parts.index("zoneinfo") + 1 :])
    except OSError:  # unreadable /etc — not worth failing startup over
        pass
    return "UTC"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- database ---------------------------------------------------------
    # Owner DSN: ETL, auth lookups, chat-history writes.
    database_url: str = Field(
        default="postgresql://qorvexa:qorvexa@127.0.0.1:5432/qorvexa"
    )
    # Read-only DSN: everything the agent's tool layer touches. Separate role,
    # SELECT only. See app/bot/db.py for why this is not just belt-and-braces.
    database_url_ro: str = Field(
        default="postgresql://qorvexa_ro:qorvexa_ro@127.0.0.1:5432/qorvexa"
    )
    pool_min_size: int = 2
    pool_max_size: int = 10
    # Server-side cap on any single statement. Replaces the old nested-thread +
    # cursor.interrupt() hack: Postgres cancels the query itself.
    statement_timeout_ms: int = 30_000
    # Tighter cap for model-authored SQL specifically.
    run_sql_timeout_ms: int = 5_000

    # --- auth -------------------------------------------------------------
    jwt_secret: str = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    jwt_ttl_hours: int = 8
    cookie_name: str = "qorvexa_session"
    cookie_secure: bool = False  # True behind HTTPS in production
    login_max_attempts: int = 5
    login_window_seconds: int = 900

    # --- model ------------------------------------------------------------
    openai_api_key: str = ""
    mr_bot_model: str = "gpt-5.6"

    # --- retrieval (Qdrant) ---
    # Leave qdrant_url empty to run the engine in-process against qdrant_path —
    # the real Qdrant, no server and no Docker. Set QDRANT_URL to point at a
    # server; the API is identical either way. Tests use ":memory:".
    qdrant_url: str = ""
    qdrant_api_key: str = ""
    qdrant_path: str = "./qdrant_data"

    # --- agenda (Gmail + Google Calendar) ---------------------------------
    # ONE OAuth client for the whole deployment, created once in the Google
    # Cloud console. This is NOT a token and NOT per-rep: it identifies the
    # application to Google. Each rep's own refresh token is encrypted and stored
    # in agenda.connections — never here, never in a log.
    #
    # Empty google_client_id = the feature is off. AgendaToolProvider then
    # contributes only the task tools, so a fresh checkout and CI both stay green
    # with nothing configured.
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/agenda/callback"

    # base64url of exactly 32 bytes:
    #   python -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
    # No default. A guessable key protecting a refresh token is worse than no
    # encryption, because it reads as protection.
    agenda_encryption_key: str = ""

    # 'readonly' can read message bodies; 'metadata' sees only headers, labels
    # and thread structure — which is enough for the entire triage view, because
    # the categories are computed from who sent the last message and when. Only
    # reading one thread needs bodies.
    agenda_gmail_scope: str = "readonly"
    # A sent mail with no reply after this many days is a follow-up due.
    # The zone "today", "overdue" and "upcoming" are judged in. Resolved in three
    # steps, most specific first, so this normally needs no configuration at all:
    #
    #   1. the CONNECTED account's own calendar_tz — Google already knows where
    #      the rep is, and this is per-rep, so a field force spanning zones is
    #      correct without anyone configuring anything (see agenda.rep_timezone)
    #   2. this setting, if someone sets AGENDA_TIMEZONE explicitly
    #   3. the host's own zone (TZ, else /etc/localtime)
    #
    # Step 3 is why AGENDA_TIMEZONE is commented out in the .env templates. A server
    # deployed for a field force sits in that field force's zone, and the machine
    # already knows which one that is; making an operator retype it was one more
    # thing to get quietly wrong.
    agenda_timezone: str = Field(default_factory=_system_timezone)

    agenda_followup_days: int = 3
    agenda_window_days: int = 14

    # --- app --------------------------------------------------------------
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    log_level: str = "INFO"
    sentry_dsn: str = ""
    environment: str = "development"

    @computed_field
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @computed_field
    @property
    def agenda_configured(self) -> bool:
        """Whether the Google integration can run at all."""
        return bool(
            self.google_client_id and self.google_client_secret and self.agenda_encryption_key
        )

    @model_validator(mode="after")
    def _agenda_is_all_or_nothing(self) -> Settings:
        """Half-configured is the state that fails at 3am rather than at startup.

        CLAUDE.md §6: prefer failing loudly at load over degrading quietly. The
        specific trap is a client id and secret with no encryption key — the
        OAuth flow would complete and then fail while storing the token, after
        the rep had already granted access at Google.
        """
        parts = (self.google_client_id, self.google_client_secret, self.agenda_encryption_key)
        if any(parts) and not all(parts):
            raise ValueError(
                "GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET and AGENDA_ENCRYPTION_KEY must be "
                "set together, or all be empty. A connection stored under a missing key is "
                "unrecoverable."
            )
        if self.agenda_encryption_key:
            try:
                raw = base64.urlsafe_b64decode(self.agenda_encryption_key)
            except (binascii.Error, ValueError) as exc:
                raise ValueError(
                    "AGENDA_ENCRYPTION_KEY must be base64url-encoded."
                ) from exc
            if len(raw) != 32:
                raise ValueError(
                    f"AGENDA_ENCRYPTION_KEY must decode to exactly 32 bytes for AES-256; "
                    f"got {len(raw)}."
                )
        if self.environment == "production" and not self.cookie_secure:
            # The session cookie IS the auth mechanism. In production it must be
            # Secure, or the 8-hour JWT travels over plaintext HTTP and is
            # interceptable. Fail at startup rather than ship a silently insecure
            # cookie — CLAUDE.md §6, and audit finding M-SEC6.
            raise ValueError(
                "COOKIE_SECURE must be true when ENVIRONMENT=production: the session "
                "cookie carries the auth token and must not be sent over plaintext HTTP."
            )
        if self.agenda_gmail_scope not in {"readonly", "metadata"}:
            raise ValueError("AGENDA_GMAIL_SCOPE must be 'readonly' or 'metadata'.")
        try:
            ZoneInfo(self.agenda_timezone)
        except Exception as exc:  # noqa: BLE001 — any zoneinfo failure is the same fault
            # Fail at import, like the other agenda settings. An unknown zone
            # discovered lazily would mean every task landed in the wrong
            # section, which reads as a logic bug rather than a typo in .env.
            raise ValueError(
                f"AGENDA_TIMEZONE {self.agenda_timezone!r} is not a known IANA zone "
                f"(e.g. 'UTC', 'Asia/Kolkata')."
            ) from exc
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
