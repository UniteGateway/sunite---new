"""scrypt password hashing (stdlib; memory-hard). Format: scrypt$n$r$p$salt_b64$hash_b64."""
from __future__ import annotations
import base64, hashlib, hmac, os

N, R, P, DKLEN = 2 ** 14, 8, 1, 32
MIN_LEN = 10
_COMMON = {"password", "password1", "password123", "1234567890", "qwertyuiop", "letmein123", "welcome123",
           "admin12345", "sunite1234", "solar12345", "iloveyou12", "abc1234567"}


class WeakPassword(ValueError):
    pass


def check_policy(pw: str, email: str = "", name: str = "") -> None:
    if len(pw) < MIN_LEN:
        raise WeakPassword(f"Password must be at least {MIN_LEN} characters")
    if len(pw) > 256:
        raise WeakPassword("Password too long")
    low = pw.lower()
    if low in _COMMON:
        raise WeakPassword("Password is too common")
    local = email.split("@")[0].lower()
    if len(local) >= 4 and local in low:
        raise WeakPassword("Password must not contain your email name")
    if len(set(pw)) < 5:
        raise WeakPassword("Password has too few distinct characters")


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=N, r=R, p=P, dklen=DKLEN, maxmem=64 * 1024 * 1024)
    b = lambda x: base64.b64encode(x).decode()
    return f"scrypt${N}${R}${P}${b(salt)}${b(h)}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, h = stored.split("$")
        if algo != "scrypt":
            return False
        calc = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                              dklen=len(base64.b64decode(h)), maxmem=64 * 1024 * 1024)
        return hmac.compare_digest(calc, base64.b64decode(h))
    except (ValueError, TypeError):
        return False


# Spent on unknown emails so login timing does not reveal which accounts exist.
DUMMY_HASH = hash_password("dummy-password-for-timing")
