from __future__ import annotations
from dataclasses import dataclass, field
from .errors import Forbidden


@dataclass(frozen=True)
class Principal:
    user_id: str
    org_id: str
    role: str
    permissions: frozenset = field(default_factory=frozenset)
    session_id: str | None = None
    is_system_admin: bool = False
    ip: str | None = None

    def require(self, perm: str):
        if perm not in self.permissions:
            raise Forbidden(f"Your role ({self.role}) does not allow '{perm}'")
