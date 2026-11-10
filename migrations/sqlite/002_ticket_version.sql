ALTER TABLE tickets ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
CREATE INDEX ix_ticket_events_ticket ON ticket_events(ticket_id, id);
