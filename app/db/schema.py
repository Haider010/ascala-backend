def ensure_installed_locations_table(cursor) -> None:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ascala_installed_locations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            connection_id UUID NOT NULL REFERENCES ascala_connections(id) ON DELETE CASCADE,
            company_id TEXT NOT NULL,
            location_id TEXT NOT NULL,
            user_id TEXT,
            user_name TEXT,
            email TEXT,
            role TEXT,
            context_type TEXT,
            is_agency_owner BOOLEAN,
            app_status TEXT,
            version_id TEXT,
            context_payload JSONB NOT NULL,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (company_id, location_id)
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_ascala_installed_locations_location_id
        ON ascala_installed_locations (location_id)
    """)


def ensure_n8n_chat_histories_metadata(cursor) -> None:
    cursor.execute("ALTER TABLE n8n_chat_histories ADD COLUMN IF NOT EXISTS location_id TEXT")
    cursor.execute("ALTER TABLE n8n_chat_histories ADD COLUMN IF NOT EXISTS agent_id TEXT")
    cursor.execute("ALTER TABLE n8n_chat_histories ADD COLUMN IF NOT EXISTS user_id TEXT")
    cursor.execute("ALTER TABLE n8n_chat_histories ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_n8n_chat_histories_session_id ON n8n_chat_histories (session_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_n8n_chat_histories_location_agent ON n8n_chat_histories (location_id, agent_id)")
