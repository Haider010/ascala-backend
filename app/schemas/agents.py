from pydantic import BaseModel


class AgentChatRequest(BaseModel):
    agentId: str
    message: str
    sessionId: str


class AgentChatResponse(BaseModel):
    payload: object
