CREATE TABLE IF NOT EXISTS tickets(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
    reporter TEXT NOT NULL, category TEXT NOT NULL,
    severity TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'reported',
    testing_round TEXT NOT NULL, area TEXT NOT NULL DEFAULT '',
    environment TEXT NOT NULL DEFAULT 'production',
    deploy_version TEXT NOT NULL DEFAULT '', assigned_to TEXT NOT NULL DEFAULT '',
    issue_url TEXT NOT NULL DEFAULT '', resolution_notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    fixed_at TEXT, deployed_at TEXT, verified_at TEXT, closed_at TEXT, fixed_round TEXT, verified_round TEXT
);
CREATE TABLE IF NOT EXISTS ticket_events(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id INTEGER NOT NULL REFERENCES tickets(id),
    old_status TEXT, new_status TEXT NOT NULL,
    actor TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS ix_tickets_round ON tickets(testing_round);
