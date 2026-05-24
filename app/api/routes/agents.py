from fastapi import APIRouter, Header

from app.core.security import get_authorization_token, verify_app_session
from app.schemas.agents import AgentChatRequest, AgentChatResponse, AgentHistoryResponse
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
