from fastapi import APIRouter, Header, HTTPException

from app.core.security import get_authorization_token, verify_app_session
from app.db.session import db_connection
from app.schemas.agents import AgentChatRequest, AgentChatResponse, AgentHistoryResponse
from app.services.account_reset import clear_account_outputs, clear_agent_output
from app.services.agents import forward_agent_chat, get_agent_history
from app.services.output_processor import process_agent_output
from app.services.workflow import build_workflow_status

router = APIRouter()


@router.post("/agent-chat", response_model=AgentChatResponse)
async def agent_chat(
    payload: AgentChatRequest,
    authorization: str | None = Header(default=None),
):
    session = verify_app_session(get_authorization_token(authorization))
    response_payload = forward_agent_chat(session, payload.agentId, payload.message, payload.sessionId)
    process_agent_output(session, payload.agentId, response_payload.get("rawPayload"), response_payload.get("sessionId"))
    location_id = session.get("activeLocation")
    if location_id:
        response_payload["workflowStatus"] = build_workflow_status(location_id)
    return response_payload


@router.get("/agent-chat/history", response_model=AgentHistoryResponse)
async def agent_chat_history(agentId: str, authorization: str | None = Header(default=None)):
    session = verify_app_session(get_authorization_token(authorization))
    return get_agent_history(session, agentId)


@router.post("/account/clear-outputs")
async def clear_outputs(authorization: str | None = Header(default=None)):
    session = verify_app_session(get_authorization_token(authorization))
    location_id = session.get("activeLocation") or session.get("locationId")
    if not location_id:
        return {"deleted": {}, "workflowStatus": None}

    with db_connection() as conn:
        cursor = conn.cursor()
        try:
            deleted = clear_account_outputs(cursor, location_id)
            workflow_status = build_workflow_status(location_id, cursor=cursor)
            conn.commit()
        finally:
            cursor.close()

    return {
        "deleted": deleted,
        "workflowStatus": workflow_status,
    }


@router.post("/account/clear-output/{agent_id}")
async def clear_single_output(agent_id: str, authorization: str | None = Header(default=None)):
    session = verify_app_session(get_authorization_token(authorization))
    location_id = session.get("activeLocation") or session.get("locationId")
    if not location_id:
        return {"agentId": agent_id, "deleted": {}, "workflowStatus": None}

    with db_connection() as conn:
        cursor = conn.cursor()
        try:
            try:
                deleted = clear_agent_output(cursor, location_id, agent_id)
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            workflow_status = build_workflow_status(location_id, cursor=cursor)
            conn.commit()
        finally:
            cursor.close()

    return {
        "agentId": agent_id,
        "deleted": deleted,
        "workflowStatus": workflow_status,
    }
