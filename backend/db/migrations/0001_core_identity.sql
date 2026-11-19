-- Organisations, users, memberships, sessions, one-time tokens, login throttling, email outbox, audit log.
-- Portable SQL (SQLite now; PostgreSQL port: TEXT ids -> UUID, TEXT timestamps -> TIMESTAMPTZ).
CREATE TABLE organizations (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL DEFAULT 'company' CHECK (kind IN ('company','franchise','customer','platform')),
    parent_org_id TEXT REFERENCES organizations(id),
    created_utc TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,           -- stored lower-cased
    full_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    email_verified_utc TEXT,
    is_system_admin INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_utc TEXT NOT NULL,
    password_changed_utc TEXT NOT NULL
);
CREATE TABLE memberships (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    created_utc TEXT NOT NULL,
    PRIMARY KEY (user_id, org_id)
);
CREATE INDEX ix_memberships_org ON memberships(org_id);
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,      -- SHA-256 of bearer token; raw token never stored
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    created_utc TEXT NOT NULL,
    last_seen_utc TEXT NOT NULL,
    expires_utc TEXT NOT NULL,
    revoked_utc TEXT,
    user_agent TEXT,
    ip TEXT
);
CREATE INDEX ix_sessions_user ON sessions(user_id);
CREATE TABLE one_time_tokens (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    purpose TEXT NOT NULL CHECK (purpose IN ('verify_email','reset_password','invite')),
    expires_utc TEXT NOT NULL,
    used_utc TEXT,
    created_utc TEXT NOT NULL
);
CREATE TABLE login_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    ip TEXT,
    success INTEGER NOT NULL,
    at_utc TEXT NOT NULL
);
CREATE INDEX ix_login_attempts_email ON login_attempts(email, at_utc);
CREATE TABLE email_outbox (
    id TEXT PRIMARY KEY,
    to_email TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    template TEXT NOT NULL,
    created_utc TEXT NOT NULL,
    sent_utc TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);
CREATE TABLE audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at_utc TEXT NOT NULL,
    org_id TEXT,
    user_id TEXT,
    action TEXT NOT NULL,
    entity_type TEXT,
    entity_id TEXT,
    detail TEXT,                           -- JSON; never contains secrets
    ip TEXT,
    request_id TEXT
);
CREATE INDEX ix_audit_org_time ON audit_logs(org_id, at_utc);
