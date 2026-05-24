import requests
from fastapi import HTTPException

from app.core.config import get_settings
from app.db.schema import ensure_n8n_chat_histories_metadata
from app.db.session import db_connection
from app.services.connections import find_connection_for_context


def get_agent_session_id(session: dict, agent_id: str, provided_session_id: str | None = None) -> str:
    location_id = session.get("activeLocation")
    if location_id:
        return f"ascala:{location_id}:{agent_id}"

    if provided_session_id:
        return provided_session_id

    raise HTTPException(status_code=400, detail="Unable to determine chat session.")


def annotate_history_session(cursor, session: dict, agent_id: str, session_id: str) -> None:
    ensure_n8n_chat_histories_metadata(cursor)
    cursor.execute(
        """
        UPDATE n8n_chat_histories
        SET
            location_id = COALESCE(location_id, %s),
            agent_id = COALESCE(agent_id, %s),
            user_id = COALESCE(user_id, %s)
        WHERE session_id = %s
        """,
        (
            session.get("activeLocation"),
            agent_id,
            session.get("userId"),
            session_id,
        ),
    )


def normalize_n8n_message(row_id: int, message: object, row_created_at=None) -> dict | None:
    role = "assistant"
    content = ""
    message_created_at = row_created_at

    if isinstance(message, str):
        content = message
    elif isinstance(message, dict):
        message_type = str(message.get("type") or message.get("role") or "").lower()
        if message_type in {"human", "user"}:
            role = "user"
        elif message_type in {"system"}:
            role = "system"
        elif message_type in {"ai", "assistant", "bot"}:
            role = "assistant"

        if isinstance(message.get("content"), str):
            content = message["content"]
        elif isinstance(message.get("text"), str):
            content = message["text"]
        elif isinstance(message.get("message"), str):
            content = message["message"]
        elif isinstance(message.get("data"), dict):
            nested = normalize_n8n_message(row_id, message["data"])
            if nested:
                return nested
        elif isinstance(message.get("kwargs"), dict):
            kwargs = message["kwargs"]
            if isinstance(kwargs.get("content"), str):
                content = kwargs["content"]
            if not message_type:
                id_path = message.get("id")
                id_text = " ".join(id_path).lower() if isinstance(id_path, list) else str(id_path or "").lower()
                if "humanmessage" in id_text:
                    role = "user"
                elif "systemmessage" in id_text:
                    role = "system"
                elif "aimessage" in id_text:
                    role = "assistant"

        message_created_at = (
            message.get("createdAt")
            or message.get("created_at")
            or message.get("timestamp")
            or row_created_at
        )
    else:
        content = str(message)

    if not content:
        return None

    return {
        "id": f"n8n-{row_id}",
        "role": role,
        "content": content,
        "createdAt": message_created_at.isoformat() if hasattr(message_created_at, "isoformat") else message_created_at,
    }


def get_agent_history(session: dict, agent_id: str) -> dict:
    settings = get_settings()
    if agent_id not in settings.agent_endpoints:
        raise HTTPException(status_code=400, detail="Unknown agent.")

    connection = find_connection_for_context(session)
    if not connection:
        raise HTTPException(status_code=403, detail="This app session is not linked to an installed account.")

    session_id = get_agent_session_id(session, agent_id)

    with db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT id, message, created_at
                FROM n8n_chat_histories
                WHERE session_id = %s
                ORDER BY id ASC
                """,
                (session_id,),
            )
            rows = cursor.fetchall()
        finally:
            cursor.close()

    messages = []
    for row_id, message, created_at in rows:
        normalized = normalize_n8n_message(row_id, message, created_at)
        if normalized:
            messages.append(normalized)

    return {
        "agentId": agent_id,
        "sessionId": session_id,
        "messages": messages,
    }


def forward_agent_chat(session: dict, agent_id: str, message: str, session_id: str | None = None) -> dict:
    settings = get_settings()
    agent_endpoints = settings.agent_endpoints

    if agent_id not in agent_endpoints:
        raise HTTPException(status_code=400, detail="Unknown agent.")
    if not message:
        raise HTTPException(status_code=400, detail="message is required.")

    connection = find_connection_for_context(session)
    if not connection:
        raise HTTPException(status_code=403, detail="This app session is not linked to an installed account.")

    agent_session_id = get_agent_session_id(session, agent_id, session_id)

    response = requests.post(
        agent_endpoints[agent_id],
        json={
            "action": "sendMessage",
            "sessionId": agent_session_id,
            "chatInput": message,
            "message": message,
            "locationId": session.get("activeLocation"),
            "companyId": session.get("companyId"),
            "userId": session.get("userId"),
            "userEmail": session.get("email"),
        },
        timeout=90,
    )

    content_type = response.headers.get("content-type", "")
    payload = response.json() if "application/json" in content_type else response.text

    if not response.ok:
        raise HTTPException(status_code=response.status_code, detail=payload)

    with db_connection() as conn:
        cursor = conn.cursor()
        try:
            annotate_history_session(cursor, session, agent_id, agent_session_id)
            conn.commit()
        finally:
            cursor.close()

    return {
        "payload": payload,
        "sessionId": agent_session_id,
    }
