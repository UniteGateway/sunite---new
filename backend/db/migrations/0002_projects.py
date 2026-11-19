"""Tenant-scoped projects with immutable revision history and explicit sharing grants.
Preserves v0.2 data: an existing un-scoped `projects` table is renamed to `legacy_projects`
so it can be imported into an organisation (services.projects.import_legacy_projects)."""


def upgrade(con):
    cols = [r[1] for r in con.execute("PRAGMA table_info(projects)")]
    if cols and "org_id" not in cols:
        con.execute("ALTER TABLE projects RENAME TO legacy_projects")
    con.executescript("""
    CREATE TABLE projects (
        id TEXT PRIMARY KEY,
        org_id TEXT NOT NULL REFERENCES organizations(id),
        name TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','in_review','approved','archived')),
        payload TEXT NOT NULL,
        revision INTEGER NOT NULL,
        created_by TEXT NOT NULL REFERENCES users(id),
        created_utc TEXT NOT NULL,
        updated_by TEXT NOT NULL REFERENCES users(id),
        updated_utc TEXT NOT NULL,
        deleted_utc TEXT
    );
    CREATE INDEX ix_projects_org ON projects(org_id, updated_utc);
    CREATE TABLE project_revisions (
        project_id TEXT NOT NULL REFERENCES projects(id),
        revision INTEGER NOT NULL,
        payload TEXT NOT NULL,
        engine_version TEXT,
        note TEXT,
        created_by TEXT NOT NULL REFERENCES users(id),
        created_utc TEXT NOT NULL,
        PRIMARY KEY (project_id, revision)
    );
    CREATE TABLE project_grants (
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        grantee_user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
        grantee_org_id TEXT REFERENCES organizations(id) ON DELETE CASCADE,
        access TEXT NOT NULL CHECK (access IN ('read','edit')),
        granted_by TEXT NOT NULL REFERENCES users(id),
        created_utc TEXT NOT NULL,
        CHECK ((grantee_user_id IS NULL) <> (grantee_org_id IS NULL))
    );
    CREATE UNIQUE INDEX ux_grant_user ON project_grants(project_id, grantee_user_id) WHERE grantee_user_id IS NOT NULL;
    CREATE UNIQUE INDEX ux_grant_org ON project_grants(project_id, grantee_org_id) WHERE grantee_org_id IS NOT NULL;
    """)
