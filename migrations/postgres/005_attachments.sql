CREATE TABLE qa_attachments(
    id TEXT PRIMARY KEY,
    ticket_id BIGINT NOT NULL REFERENCES tickets(id),
    uploader_subject TEXT,
    uploader_name TEXT NOT NULL,
    object_key TEXT NOT NULL UNIQUE,
    generation TEXT,
    state TEXT NOT NULL CHECK(state IN ('pending','ready','failed','deleting')),
    mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    checksum TEXT NOT NULL,
    created_at BIGINT NOT NULL
);
CREATE INDEX ix_qa_attachments_ticket ON qa_attachments(ticket_id, state);
CREATE INDEX ix_qa_attachments_cleanup ON qa_attachments(state, created_at);
