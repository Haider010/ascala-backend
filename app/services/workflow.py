from app.db.schema import ensure_agent_outputs_tables
from app.db.session import db_connection

WORKFLOW_STEPS = [
    {"id": "molly", "name": "Molly\u2122", "role": "Audience Intelligence", "table": "molly_outputs", "available": True},
    {"id": "brandy", "name": "Brandy\u2122", "role": "Brand Voice", "table": "brandy_outputs", "available": True},
    {"id": "sacha", "name": "Sacha\u2122", "role": "Strategy Director", "table": "sacha_outputs", "available": True},
    {"id": "escouade", "name": "Escouade\u2122", "role": "AI Production Team", "table": None, "available": True},
    {"id": "uply", "name": "Uply\u2122", "role": "Publishing Assistant", "table": None, "available": False},
]


def get_completed_outputs(location_id: str, cursor) -> set[str]:
    completed = set()
    ensure_agent_outputs_tables(cursor)

    for step in WORKFLOW_STEPS:
        table_name = step.get("table")
        if not table_name:
            continue

        cursor.execute(
            f"""
            SELECT 1
            FROM {table_name}
            WHERE location_id = %s
              AND COALESCE(final_output, '') <> ''
            LIMIT 1
            """,
            (location_id,),
        )
        if cursor.fetchone():
            completed.add(step["id"])

    return completed


def build_workflow_status(location_id: str, cursor=None) -> dict:
    if cursor:
        completed = get_completed_outputs(location_id, cursor)
    else:
        with db_connection() as conn:
            local_cursor = conn.cursor()
            try:
                completed = get_completed_outputs(location_id, local_cursor)
            finally:
                local_cursor.close()

    first_incomplete_index = next(
        (index for index, step in enumerate(WORKFLOW_STEPS) if step["id"] not in completed),
        len(WORKFLOW_STEPS) - 1,
    )
    current_agent_id = WORKFLOW_STEPS[first_incomplete_index]["id"]

    steps = []
    for index, step in enumerate(WORKFLOW_STEPS):
        is_completed = step["id"] in completed
        is_current = index == first_incomplete_index
        is_locked = not is_completed and not is_current

        steps.append({
            "id": step["id"],
            "name": step["name"],
            "role": step["role"],
            "status": "completed" if is_completed else "current" if is_current else "locked",
            "locked": is_locked,
            "completed": is_completed,
            "available": step["available"],
        })

    return {
        "currentAgentId": current_agent_id,
        "steps": steps,
    }
