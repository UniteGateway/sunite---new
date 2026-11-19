"""Roles and permissions. Enforcement happens in the service layer, not only in the UI."""
from __future__ import annotations

PERMISSIONS = {
    "org.manage", "org.members.manage", "audit.read",
    "project.create", "project.read", "project.update", "project.delete", "project.share", "project.approve",
    "design.layout", "design.electrical", "design.electrical.approve", "design.structural",
    "design.structural.approve", "boq.edit", "boq.price", "boq.approve",
    "procurement.manage", "finance.edit", "proposal.issue", "crm.manage", "epc.manage",
    "equipment.manage", "equipment.verify", "procurement.approve", "quotation.approve",
}

_DESIGN_READ = {"project.read"}
ROLES: dict[str, set[str]] = {
    "company_admin": PERMISSIONS - {"design.electrical.approve", "design.structural.approve", "equipment.verify"},
    "project_manager": {"project.create", "project.read", "project.update", "project.share", "project.approve",
                        "design.layout", "boq.edit", "boq.approve", "epc.manage", "audit.read", "procurement.approve",
                        "crm.manage", "quotation.approve"},
    "solar_designer": {"project.create", "project.read", "project.update", "design.layout", "boq.edit", "equipment.manage"},
    "electrical_engineer": {"project.read", "project.update", "design.electrical", "design.electrical.approve", "boq.edit",
                            "equipment.manage", "equipment.verify", "epc.manage"},
    "structural_engineer": {"project.read", "project.update", "design.structural", "design.structural.approve", "boq.edit"},
    "sales_executive": {"project.create", "project.read", "finance.edit", "proposal.issue", "crm.manage"},
    "procurement_executive": {"project.read", "boq.price", "procurement.manage", "equipment.manage"},
    "franchise_partner": {"project.create", "project.read", "project.update", "design.layout", "finance.edit",
                          "proposal.issue", "crm.manage"},
    "customer": set(_DESIGN_READ),
}
# Engineering approvals are deliberately NOT given to company_admin: sign-off must come from the
# relevant qualified engineer role. system_admin is a platform flag on the user, not an org role.
assert all(p <= PERMISSIONS for p in ROLES.values())
ROLE_NAMES = tuple(ROLES)
# Roles that only see projects explicitly shared with them, never the whole org list.
GRANT_ONLY_ROLES = {"customer"}


def permissions_for(role: str) -> set[str]:
    if role not in ROLES:
        raise ValueError(f"Unknown role {role}")
    return ROLES[role]
