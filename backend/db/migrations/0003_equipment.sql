-- Equipment catalogue with four-eyes datasheet verification. org_id NULL = platform-wide catalogue.
CREATE TABLE equipment (
    id TEXT PRIMARY KEY,
    org_id TEXT REFERENCES organizations(id),
    kind TEXT NOT NULL CHECK (kind IN ('module','inverter')),
    manufacturer TEXT NOT NULL,
    model TEXT NOT NULL,
    specs TEXT NOT NULL,                  -- JSON, validated by services.equipment
    datasheet_url TEXT,
    datasheet_sha256 TEXT,
    datasheet_revision TEXT,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','pending_verification','verified','retired')),
    revision INTEGER NOT NULL DEFAULT 1,
    entered_by TEXT NOT NULL REFERENCES users(id),
    verified_by TEXT REFERENCES users(id),
    verified_utc TEXT,
    created_utc TEXT NOT NULL,
    updated_utc TEXT NOT NULL
);
CREATE UNIQUE INDEX ux_equipment_model ON equipment(COALESCE(org_id,'*'), kind, manufacturer, model);
CREATE INDEX ix_equipment_org_kind ON equipment(org_id, kind, status);
CREATE TABLE equipment_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equipment_id TEXT NOT NULL REFERENCES equipment(id),
    revision INTEGER NOT NULL,
    action TEXT NOT NULL,
    specs TEXT NOT NULL,
    by_user TEXT NOT NULL REFERENCES users(id),
    at_utc TEXT NOT NULL,
    note TEXT
);
