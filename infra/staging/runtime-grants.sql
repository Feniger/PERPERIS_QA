-- PERPERIS QA staging: least-privilege access for the app's runtime database role.
-- Applied by the "Deploy staging" workflow after migrations, as the MIGRATION role
-- (the schema owner). Safe to re-run: it resets qa_runtime to exactly these rights.
-- The role itself (qa_runtime, with its own password) is created in the Neon console.
-- Comments here must not contain semicolons: the workflow splits statements on them.

GRANT USAGE ON SCHEMA public TO qa_runtime;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM qa_runtime;

-- Read-only bookkeeping
GRANT SELECT ON schema_migrations, data_imports TO qa_runtime;

-- Working data
GRANT SELECT, INSERT, UPDATE ON tickets, qa_members TO qa_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON qa_sessions, qa_auth_throttle, qa_attachments TO qa_runtime;

-- Audit history is append-only for the app: no UPDATE or DELETE
GRANT SELECT, INSERT ON ticket_events, ticket_changes, qa_security_events TO qa_runtime;

-- Identity columns
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO qa_runtime;
