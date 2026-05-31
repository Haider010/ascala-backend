from app.db.schema import ensure_agent_outputs_tables, ensure_n8n_chat_histories_metadata


def clear_account_outputs(cursor, location_id: str) -> dict[str, int]:
    ensure_agent_outputs_tables(cursor)
    ensure_n8n_chat_histories_metadata(cursor)

    deleted = {}

    for table_name in ("molly_outputs", "brandy_outputs", "sacha_outputs"):
        cursor.execute(f"DELETE FROM {table_name} WHERE location_id = %s", (location_id,))
        deleted[table_name] = cursor.rowcount

    cursor.execute("DELETE FROM escouade_items WHERE location_id = %s", (location_id,))
    deleted["escouade_items"] = cursor.rowcount

    cursor.execute("DELETE FROM escouade_batches WHERE location_id = %s", (location_id,))
    deleted["escouade_batches"] = cursor.rowcount

    cursor.execute(
        """
        DELETE FROM n8n_chat_histories
        WHERE location_id = %s
           OR session_id LIKE %s
        """,
        (location_id, f"ascala:{location_id}:%"),
    )
    deleted["n8n_chat_histories"] = cursor.rowcount

    return deleted
