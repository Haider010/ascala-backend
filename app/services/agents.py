import requests
from fastapi import HTTPException

from app.core.config import get_settings
from app.services.connections import find_connection_for_context


def forward_agent_chat(session: dict, agent_id: str, message: str, session_id: str) -> object:
    settings = get_settings()
    agent_endpoints = settings.agent_endpoints

    if agent_id not in agent_endpoints:
        raise HTTPException(status_code=400, detail="Unknown agent.")
    if not message or not session_id:
        raise HTTPException(status_code=400, detail="message and sessionId are required.")

    connection = find_connection_for_context(session)
    if not connection:
        raise HTTPException(status_code=403, detail="This app session is not linked to an installed account.")

    response = requests.post(
        agent_endpoints[agent_id],
        json={
            "action": "sendMessage",
            "sessionId": session_id,
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

    return payload
