"""Environment-driven settings. No secrets in code; see .env.example."""
from __future__ import annotations
import os
from dataclasses import dataclass, field


def _int(name, default):
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    env: str = field(default_factory=lambda: os.environ.get("SUNITE_ENV", "development"))
    db_path: str = field(default_factory=lambda: os.environ.get("SUNITE_DB_PATH", "sunite.sqlite3"))
    # "required": every project route needs a session. "local_demo": v0.2 single-user mode (refused in production).
    auth_mode: str = field(default_factory=lambda: os.environ.get("SUNITE_AUTH_MODE", "required"))
    session_ttl_min: int = field(default_factory=lambda: _int("SUNITE_SESSION_TTL_MIN", 60 * 12))
    session_idle_min: int = field(default_factory=lambda: _int("SUNITE_SESSION_IDLE_MIN", 120))
    reset_token_ttl_min: int = field(default_factory=lambda: _int("SUNITE_RESET_TTL_MIN", 30))
    verify_token_ttl_min: int = field(default_factory=lambda: _int("SUNITE_VERIFY_TTL_MIN", 60 * 48))
    max_failed_logins: int = field(default_factory=lambda: _int("SUNITE_MAX_FAILED_LOGINS", 5))
    lockout_window_min: int = field(default_factory=lambda: _int("SUNITE_LOCKOUT_MIN", 15))
    app_base_url: str = field(default_factory=lambda: os.environ.get("SUNITE_BASE_URL", "http://localhost:8000"))

    def validate(self):
        if self.env == "production" and self.auth_mode != "required":
            raise RuntimeError("SUNITE_AUTH_MODE must be 'required' in production")
        if self.auth_mode not in ("required", "local_demo"):
            raise RuntimeError("SUNITE_AUTH_MODE must be 'required' or 'local_demo'")
        return self


def get_settings() -> Settings:
    return Settings().validate()
