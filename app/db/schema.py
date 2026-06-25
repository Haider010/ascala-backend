def ensure_connections_table(cursor) -> None:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ascala_connections (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            access_token TEXT,
            refresh_token TEXT,
            token_type TEXT,
            expires_in INTEGER,
            scope TEXT,
            refresh_token_id TEXT,
            company_id TEXT NOT NULL,
            user_id TEXT,
            user_type TEXT,
            is_bulk_installation BOOLEAN,
            api_key TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    cursor.execute("ALTER TABLE ascala_connections ADD COLUMN IF NOT EXISTS access_token TEXT")
    cursor.execute("ALTER TABLE ascala_connections ADD COLUMN IF NOT EXISTS refresh_token TEXT")
    cursor.execute("ALTER TABLE ascala_connections ADD COLUMN IF NOT EXISTS token_type TEXT")
    cursor.execute("ALTER TABLE ascala_connections ADD COLUMN IF NOT EXISTS expires_in INTEGER")
    cursor.execute("ALTER TABLE ascala_connections ADD COLUMN IF NOT EXISTS scope TEXT")
    cursor.execute("ALTER TABLE ascala_connections ADD COLUMN IF NOT EXISTS refresh_token_id TEXT")
    cursor.execute("ALTER TABLE ascala_connections ADD COLUMN IF NOT EXISTS company_id TEXT")
    cursor.execute("ALTER TABLE ascala_connections ADD COLUMN IF NOT EXISTS user_id TEXT")
    cursor.execute("ALTER TABLE ascala_connections ADD COLUMN IF NOT EXISTS user_type TEXT")
    cursor.execute("ALTER TABLE ascala_connections ADD COLUMN IF NOT EXISTS is_bulk_installation BOOLEAN")
    cursor.execute("ALTER TABLE ascala_connections ADD COLUMN IF NOT EXISTS api_key TEXT")
    cursor.execute("ALTER TABLE ascala_connections ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()")
    cursor.execute("ALTER TABLE ascala_connections ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()")
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_ascala_connections_company_id
        ON ascala_connections (company_id)
    """)


def ensure_installed_locations_table(cursor) -> None:
    ensure_connections_table(cursor)
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
            location_access_token TEXT,
            location_refresh_token TEXT,
            location_token_type TEXT,
            location_token_scope TEXT,
            location_refresh_token_id TEXT,
            location_token_expires_at TIMESTAMPTZ,
            location_token_updated_at TIMESTAMPTZ,
            context_payload JSONB NOT NULL,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (company_id, location_id)
        )
    """)
    cursor.execute("ALTER TABLE ascala_installed_locations ADD COLUMN IF NOT EXISTS location_access_token TEXT")
    cursor.execute("ALTER TABLE ascala_installed_locations ADD COLUMN IF NOT EXISTS location_refresh_token TEXT")
    cursor.execute("ALTER TABLE ascala_installed_locations ADD COLUMN IF NOT EXISTS location_token_type TEXT")
    cursor.execute("ALTER TABLE ascala_installed_locations ADD COLUMN IF NOT EXISTS location_token_scope TEXT")
    cursor.execute("ALTER TABLE ascala_installed_locations ADD COLUMN IF NOT EXISTS location_refresh_token_id TEXT")
    cursor.execute("ALTER TABLE ascala_installed_locations ADD COLUMN IF NOT EXISTS location_token_expires_at TIMESTAMPTZ")
    cursor.execute("ALTER TABLE ascala_installed_locations ADD COLUMN IF NOT EXISTS location_token_updated_at TIMESTAMPTZ")
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


def ensure_agent_outputs_tables(cursor) -> None:
    for table_name in ("molly_outputs", "brandy_outputs", "sacha_outputs"):
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                location_id TEXT NOT NULL UNIQUE,
                final_output TEXT NOT NULL,
                structured_output JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                output_type TEXT,
                metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                version INTEGER NOT NULL DEFAULT 1,
                source_session_id TEXT,
                source_message_id TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS structured_output JSONB NOT NULL DEFAULT '{{}}'::jsonb")
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS output_type TEXT")
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb")
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1")
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS source_session_id TEXT")
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS source_message_id TEXT")
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()")
        cursor.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{table_name}_location_id_unique ON {table_name} (location_id)")


def ensure_brandboard_outputs_table(cursor) -> None:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS brandboard_outputs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            location_id TEXT NOT NULL UNIQUE,
            final_output TEXT NOT NULL,
            structured_output JSONB NOT NULL DEFAULT '{}'::jsonb,
            output_type TEXT NOT NULL DEFAULT 'brand_guidelines_100x',
            theme TEXT,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            version INTEGER NOT NULL DEFAULT 1,
            source_molly_updated_at TIMESTAMPTZ,
            source_brandy_updated_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    cursor.execute("ALTER TABLE brandboard_outputs ADD COLUMN IF NOT EXISTS final_output TEXT NOT NULL DEFAULT ''")
    cursor.execute("ALTER TABLE brandboard_outputs ADD COLUMN IF NOT EXISTS structured_output JSONB NOT NULL DEFAULT '{}'::jsonb")
    cursor.execute("ALTER TABLE brandboard_outputs ADD COLUMN IF NOT EXISTS output_type TEXT NOT NULL DEFAULT 'brand_guidelines_100x'")
    cursor.execute("ALTER TABLE brandboard_outputs ADD COLUMN IF NOT EXISTS theme TEXT")
    cursor.execute("ALTER TABLE brandboard_outputs ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb")
    cursor.execute("ALTER TABLE brandboard_outputs ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1")
    cursor.execute("ALTER TABLE brandboard_outputs ADD COLUMN IF NOT EXISTS source_molly_updated_at TIMESTAMPTZ")
    cursor.execute("ALTER TABLE brandboard_outputs ADD COLUMN IF NOT EXISTS source_brandy_updated_at TIMESTAMPTZ")
    cursor.execute("ALTER TABLE brandboard_outputs ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()")
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_brandboard_outputs_location_id_unique ON brandboard_outputs (location_id)")


def ensure_escouade_chat_messages_table(cursor) -> None:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS escouade_chat_messages (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            location_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    cursor.execute("ALTER TABLE escouade_chat_messages ADD COLUMN IF NOT EXISTS location_id TEXT")
    cursor.execute("ALTER TABLE escouade_chat_messages ADD COLUMN IF NOT EXISTS role TEXT NOT NULL DEFAULT 'assistant'")
    cursor.execute("ALTER TABLE escouade_chat_messages ADD COLUMN IF NOT EXISTS content TEXT NOT NULL DEFAULT ''")
    cursor.execute("ALTER TABLE escouade_chat_messages ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb")
    cursor.execute("ALTER TABLE escouade_chat_messages ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()")
    cursor.execute("DROP INDEX IF EXISTS idx_escouade_chat_location_thread")
    cursor.execute("DROP INDEX IF EXISTS idx_escouade_chat_batch_created")
    cursor.execute("ALTER TABLE escouade_chat_messages DROP COLUMN IF EXISTS batch_id")
    cursor.execute("ALTER TABLE escouade_chat_messages DROP COLUMN IF EXISTS thread_type")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_escouade_chat_location_created ON escouade_chat_messages (location_id, created_at)")


def ensure_token_usage_table(cursor) -> None:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ascala_token_usage_monthly (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            usage_month DATE NOT NULL,
            company_id TEXT,
            location_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            model TEXT,
            input_tokens BIGINT NOT NULL DEFAULT 0,
            fresh_input_tokens BIGINT NOT NULL DEFAULT 0,
            cached_input_tokens BIGINT NOT NULL DEFAULT 0,
            output_tokens BIGINT NOT NULL DEFAULT 0,
            total_tokens BIGINT NOT NULL DEFAULT 0,
            call_count BIGINT NOT NULL DEFAULT 0,
            last_usage_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (usage_month, location_id, user_id, agent_id)
        )
    """)
    cursor.execute("ALTER TABLE ascala_token_usage_monthly ADD COLUMN IF NOT EXISTS company_id TEXT")
    cursor.execute("ALTER TABLE ascala_token_usage_monthly ADD COLUMN IF NOT EXISTS model TEXT")
    cursor.execute("ALTER TABLE ascala_token_usage_monthly ADD COLUMN IF NOT EXISTS fresh_input_tokens BIGINT NOT NULL DEFAULT 0")
    cursor.execute("ALTER TABLE ascala_token_usage_monthly ADD COLUMN IF NOT EXISTS cached_input_tokens BIGINT NOT NULL DEFAULT 0")
    cursor.execute("ALTER TABLE ascala_token_usage_monthly ADD COLUMN IF NOT EXISTS last_usage_metadata JSONB NOT NULL DEFAULT '{}'::jsonb")
    cursor.execute("""
        UPDATE ascala_token_usage_monthly
        SET fresh_input_tokens = input_tokens
        WHERE input_tokens > 0
          AND fresh_input_tokens = 0
          AND cached_input_tokens = 0
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ascala_token_usage_monthly_location_month ON ascala_token_usage_monthly (location_id, usage_month)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ascala_token_usage_monthly_user_month ON ascala_token_usage_monthly (user_id, usage_month)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ascala_token_usage_monthly_location_agent_model ON ascala_token_usage_monthly (location_id, usage_month, agent_id, model)")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ascala_token_usage_events (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            usage_month DATE NOT NULL,
            company_id TEXT,
            location_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            model TEXT,
            response_id TEXT,
            input_tokens BIGINT NOT NULL DEFAULT 0,
            fresh_input_tokens BIGINT NOT NULL DEFAULT 0,
            cached_input_tokens BIGINT NOT NULL DEFAULT 0,
            output_tokens BIGINT NOT NULL DEFAULT 0,
            total_tokens BIGINT NOT NULL DEFAULT 0,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            usage_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            response_metadata_usage JSONB NOT NULL DEFAULT '{}'::jsonb
        )
    """)
    cursor.execute("ALTER TABLE ascala_token_usage_events ADD COLUMN IF NOT EXISTS company_id TEXT")
    cursor.execute("ALTER TABLE ascala_token_usage_events ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'unknown'")
    cursor.execute("ALTER TABLE ascala_token_usage_events ADD COLUMN IF NOT EXISTS response_id TEXT")
    cursor.execute("ALTER TABLE ascala_token_usage_events ADD COLUMN IF NOT EXISTS fresh_input_tokens BIGINT NOT NULL DEFAULT 0")
    cursor.execute("ALTER TABLE ascala_token_usage_events ADD COLUMN IF NOT EXISTS cached_input_tokens BIGINT NOT NULL DEFAULT 0")
    cursor.execute("ALTER TABLE ascala_token_usage_events ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb")
    cursor.execute("ALTER TABLE ascala_token_usage_events ADD COLUMN IF NOT EXISTS usage_metadata JSONB NOT NULL DEFAULT '{}'::jsonb")
    cursor.execute("ALTER TABLE ascala_token_usage_events ADD COLUMN IF NOT EXISTS response_metadata_usage JSONB NOT NULL DEFAULT '{}'::jsonb")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ascala_token_usage_events_location_recorded ON ascala_token_usage_events (location_id, recorded_at DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ascala_token_usage_events_location_month ON ascala_token_usage_events (location_id, usage_month)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ascala_token_usage_events_location_agent_model ON ascala_token_usage_events (location_id, usage_month, agent_id, model)")
