from fastapi import APIRouter, Header

from app.core.security import get_authorization_token, verify_app_session
from app.schemas.agents import AgentChatRequest, AgentChatResponse
from app.services.agents import forward_agent_chat

router = APIRouter()


@router.post("/agent-chat", response_model=AgentChatResponse)
async def agent_chat(payload: AgentChatRequest, authorization: str | None = Header(default=None)):
    session = verify_app_session(get_authorization_token(authorization))
    response_payload = forward_agent_chat(session, payload.agentId, payload.message, payload.sessionId)
    return {"payload": response_payload}
