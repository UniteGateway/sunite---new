"""Opaque random tokens; only SHA-256 digests are persisted."""
import hashlib, secrets


def new_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    return raw, digest(raw)


def digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
