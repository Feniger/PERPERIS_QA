CREATE TABLE data_imports(
    fingerprint TEXT PRIMARY KEY,
    ticket_count INTEGER NOT NULL,
    event_count INTEGER NOT NULL
);
