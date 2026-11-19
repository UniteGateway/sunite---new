-- CRM (Stage 17), procurement (Stage 14), EPC execution (Stage 18). All rows are tenant-scoped by org_id.
CREATE TABLE customers (
    id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES organizations(id), name TEXT NOT NULL,
    segment TEXT NOT NULL DEFAULT 'residential' CHECK (segment IN ('residential','commercial','industrial','institutional','government','utility')),
    phone TEXT, email TEXT, address TEXT, city TEXT, state TEXT, created_utc TEXT NOT NULL, created_by TEXT NOT NULL REFERENCES users(id)
);
CREATE INDEX ix_customers_org ON customers(org_id, name);
CREATE TABLE leads (
    id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES organizations(id), customer_id TEXT NOT NULL REFERENCES customers(id),
    title TEXT NOT NULL, source TEXT, stage TEXT NOT NULL, owner_user_id TEXT REFERENCES users(id), est_kwp REAL,
    project_id TEXT REFERENCES projects(id), next_follow_up_utc TEXT, lost_reason TEXT,
    created_utc TEXT NOT NULL, updated_utc TEXT NOT NULL
);
CREATE INDEX ix_leads_org_stage ON leads(org_id, stage);
CREATE TABLE lead_activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT, org_id TEXT NOT NULL, lead_id TEXT NOT NULL REFERENCES leads(id),
    kind TEXT NOT NULL, note TEXT, at_utc TEXT NOT NULL, by_user TEXT NOT NULL REFERENCES users(id)
);
CREATE TABLE quotations (
    id TEXT PRIMARY KEY, org_id TEXT NOT NULL, lead_id TEXT NOT NULL REFERENCES leads(id), project_id TEXT NOT NULL REFERENCES projects(id),
    project_revision INTEGER NOT NULL, version INTEGER NOT NULL, model TEXT NOT NULL, total REAL NOT NULL, currency TEXT NOT NULL,
    valid_until TEXT, snapshot TEXT NOT NULL, status TEXT NOT NULL CHECK (status IN ('draft','approved','sent','accepted','rejected','superseded')),
    created_by TEXT NOT NULL, approved_by TEXT, created_utc TEXT NOT NULL, UNIQUE (lead_id, version)
);
CREATE TABLE vendors (
    id TEXT PRIMARY KEY, org_id TEXT NOT NULL, name TEXT NOT NULL, gstin TEXT, email TEXT, phone TEXT, categories TEXT,
    approved INTEGER NOT NULL DEFAULT 0, created_utc TEXT NOT NULL, UNIQUE (org_id, name)
);
CREATE TABLE rfqs (
    id TEXT PRIMARY KEY, org_id TEXT NOT NULL, project_id TEXT NOT NULL REFERENCES projects(id), project_revision INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft','issued','closed')), due_utc TEXT, items TEXT NOT NULL,
    created_by TEXT NOT NULL, created_utc TEXT NOT NULL
);
CREATE TABLE rfq_vendors (rfq_id TEXT NOT NULL REFERENCES rfqs(id), vendor_id TEXT NOT NULL REFERENCES vendors(id), PRIMARY KEY (rfq_id, vendor_id));
CREATE TABLE vendor_quotes (
    id TEXT PRIMARY KEY, org_id TEXT NOT NULL, rfq_id TEXT NOT NULL REFERENCES rfqs(id), vendor_id TEXT NOT NULL REFERENCES vendors(id),
    lines TEXT NOT NULL, delivery_days INTEGER, valid_until TEXT, received_utc TEXT NOT NULL, entered_by TEXT NOT NULL,
    UNIQUE (rfq_id, vendor_id)
);
CREATE TABLE purchase_orders (
    id TEXT PRIMARY KEY, org_id TEXT NOT NULL, number TEXT NOT NULL, rfq_id TEXT REFERENCES rfqs(id), vendor_id TEXT NOT NULL REFERENCES vendors(id),
    project_id TEXT REFERENCES projects(id), lines TEXT NOT NULL, subtotal REAL NOT NULL, tax REAL NOT NULL, total REAL NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending_approval','approved','issued','cancelled','closed')),
    created_by TEXT NOT NULL, approved_by TEXT, approved_utc TEXT, issued_utc TEXT, created_utc TEXT NOT NULL, UNIQUE (org_id, number)
);
CREATE TABLE goods_receipts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, org_id TEXT NOT NULL, po_id TEXT NOT NULL REFERENCES purchase_orders(id),
    line_no INTEGER NOT NULL, qty REAL NOT NULL, location TEXT, received_by TEXT NOT NULL, at_utc TEXT NOT NULL
);
CREATE TABLE inventory (
    org_id TEXT NOT NULL, code TEXT NOT NULL, location TEXT NOT NULL, item TEXT NOT NULL, unit TEXT NOT NULL, qty REAL NOT NULL,
    PRIMARY KEY (org_id, code, location)
);
CREATE TABLE inventory_moves (
    id INTEGER PRIMARY KEY AUTOINCREMENT, org_id TEXT NOT NULL, code TEXT NOT NULL, location TEXT NOT NULL, qty REAL NOT NULL,
    reason TEXT NOT NULL, ref TEXT, by_user TEXT NOT NULL, at_utc TEXT NOT NULL
);
CREATE TABLE epc_projects (
    id TEXT PRIMARY KEY, org_id TEXT NOT NULL, project_id TEXT NOT NULL UNIQUE REFERENCES projects(id), lead_id TEXT REFERENCES leads(id),
    quotation_id TEXT REFERENCES quotations(id), status TEXT NOT NULL CHECK (status IN ('active','commissioned','handed_over','on_hold')),
    start_date TEXT NOT NULL, created_utc TEXT NOT NULL, handed_over_utc TEXT
);
CREATE TABLE epc_tasks (
    id TEXT PRIMARY KEY, epc_id TEXT NOT NULL REFERENCES epc_projects(id), wbs TEXT NOT NULL, name TEXT NOT NULL, phase TEXT NOT NULL,
    planned_start TEXT NOT NULL, planned_end TEXT NOT NULL, depends_on TEXT, assignee TEXT,
    status TEXT NOT NULL DEFAULT 'not_started' CHECK (status IN ('not_started','in_progress','done','blocked')), progress_pct REAL NOT NULL DEFAULT 0
);
CREATE TABLE epc_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, epc_id TEXT NOT NULL REFERENCES epc_projects(id), log_date TEXT NOT NULL,
    note TEXT NOT NULL, manpower INTEGER, photos TEXT, by_user TEXT NOT NULL, at_utc TEXT NOT NULL
);
CREATE TABLE epc_checklists (
    id TEXT PRIMARY KEY, epc_id TEXT NOT NULL REFERENCES epc_projects(id), kind TEXT NOT NULL CHECK (kind IN ('quality','inspection','commissioning','handover')),
    items TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','signed_off')), signed_by TEXT, signed_utc TEXT
);
CREATE TABLE punch_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT, epc_id TEXT NOT NULL REFERENCES epc_projects(id), description TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('critical','major','minor')), status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
    raised_by TEXT NOT NULL, closed_by TEXT, raised_utc TEXT NOT NULL, closed_utc TEXT
);
CREATE TABLE om_schedule (
    id INTEGER PRIMARY KEY AUTOINCREMENT, epc_id TEXT NOT NULL REFERENCES epc_projects(id), activity TEXT NOT NULL,
    due_date TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'scheduled'
);
CREATE TABLE warranties (
    id INTEGER PRIMARY KEY AUTOINCREMENT, epc_id TEXT NOT NULL REFERENCES epc_projects(id), item TEXT NOT NULL,
    provider TEXT, years REAL, starts TEXT NOT NULL, ends TEXT NOT NULL, reference TEXT
);
