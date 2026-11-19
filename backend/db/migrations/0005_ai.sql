-- AI assistant (Stage 19): every exchange logged; design changes are proposals needing human approval.
CREATE TABLE ai_conversations (
    id TEXT PRIMARY KEY, org_id TEXT NOT NULL, user_id TEXT NOT NULL, project_id TEXT, question TEXT NOT NULL,
    answer TEXT, tool_calls TEXT NOT NULL, grounding TEXT, provider TEXT NOT NULL, at_utc TEXT NOT NULL
);
CREATE TABLE ai_proposals (
    id TEXT PRIMARY KEY, org_id TEXT NOT NULL, project_id TEXT NOT NULL REFERENCES projects(id), base_revision INTEGER NOT NULL,
    changes TEXT NOT NULL, kpi_before TEXT NOT NULL, kpi_after TEXT NOT NULL, rationale TEXT,
    status TEXT NOT NULL CHECK (status IN ('pending','applied','rejected','stale')), created_by TEXT NOT NULL,
    decided_by TEXT, decided_utc TEXT, created_utc TEXT NOT NULL
);
