from app.db.schema import (
    ensure_agent_outputs_tables,
    ensure_brandboard_outputs_table,
    ensure_escouade_chat_messages_table,
    ensure_n8n_chat_histories_metadata,
)


CHAT_AGENT_IDS = {"molly", "brandy", "sacha"}
OUTPUT_TABLE_BY_AGENT = {
    "molly": "molly_outputs",
    "brandy": "brandy_outputs",
    "sacha": "sacha_outputs",
    "brandboard": "brandboard_outputs",
}
SUPPORTED_CLEAR_AGENT_IDS = {*OUTPUT_TABLE_BY_AGENT, "escouade", "uply"}


def _delete_agent_chat_history(cursor, location_id: str, agent_id: str) -> int:
    ensure_n8n_chat_histories_metadata(cursor)
    cursor.execute(
        """
        DELETE FROM n8n_chat_histories
        WHERE (location_id = %s AND agent_id = %s)
           OR session_id LIKE %s
        """,
        (location_id, agent_id, f"ascala:{location_id}:{agent_id}%"),
    )
    return cursor.rowcount


def clear_agent_output(cursor, location_id: str, agent_id: str) -> dict[str, int]:
    if agent_id not in SUPPORTED_CLEAR_AGENT_IDS:
        raise ValueError(f"Unsupported agent id: {agent_id}")

    ensure_agent_outputs_tables(cursor)
    ensure_brandboard_outputs_table(cursor)
    ensure_escouade_chat_messages_table(cursor)
    ensure_n8n_chat_histories_metadata(cursor)

    deleted = {}

    table_name = OUTPUT_TABLE_BY_AGENT.get(agent_id)
    if table_name:
        cursor.execute(f"DELETE FROM {table_name} WHERE location_id = %s", (location_id,))
        deleted[table_name] = cursor.rowcount

    if agent_id in CHAT_AGENT_IDS:
        deleted["n8n_chat_histories"] = _delete_agent_chat_history(cursor, location_id, agent_id)

    if agent_id == "escouade":
        cursor.execute("DELETE FROM escouade_chat_messages WHERE location_id = %s", (location_id,))
        deleted["escouade_chat_messages"] = cursor.rowcount

        cursor.execute("DELETE FROM escouade_production_rows WHERE location_id = %s", (location_id,))
        deleted["escouade_production_rows"] = cursor.rowcount

        cursor.execute("DELETE FROM escouade_production_tables WHERE location_id = %s", (location_id,))
        deleted["escouade_production_tables"] = cursor.rowcount

        cursor.execute("DELETE FROM escouade_items WHERE location_id = %s", (location_id,))
        deleted["escouade_items"] = cursor.rowcount

        cursor.execute("DELETE FROM escouade_batches WHERE location_id = %s", (location_id,))
        deleted["escouade_batches"] = cursor.rowcount

    if agent_id == "uply":
        deleted["uply_outputs"] = 0

    return deleted


def clear_account_outputs(cursor, location_id: str) -> dict[str, int]:
    ensure_agent_outputs_tables(cursor)
    ensure_brandboard_outputs_table(cursor)
    ensure_escouade_chat_messages_table(cursor)
    ensure_n8n_chat_histories_metadata(cursor)

    deleted = {}

    for table_name in ("molly_outputs", "brandy_outputs", "sacha_outputs"):
        cursor.execute(f"DELETE FROM {table_name} WHERE location_id = %s", (location_id,))
        deleted[table_name] = cursor.rowcount

    cursor.execute("DELETE FROM brandboard_outputs WHERE location_id = %s", (location_id,))
    deleted["brandboard_outputs"] = cursor.rowcount

    cursor.execute("DELETE FROM escouade_chat_messages WHERE location_id = %s", (location_id,))
    deleted["escouade_chat_messages"] = cursor.rowcount

    cursor.execute("DELETE FROM escouade_production_rows WHERE location_id = %s", (location_id,))
    deleted["escouade_production_rows"] = cursor.rowcount

    cursor.execute("DELETE FROM escouade_production_tables WHERE location_id = %s", (location_id,))
    deleted["escouade_production_tables"] = cursor.rowcount

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
