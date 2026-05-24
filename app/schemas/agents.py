from pydantic import BaseModel


class AgentChatRequest(BaseModel):
    agentId: str
    message: str
    sessionId: str | None = None


class AgentChatResponse(BaseModel):
    payload: object
    sessionId: str


class AgentHistoryMessage(BaseModel):
    id: str
    role: str
    content: str
    createdAt: str | None = None


class AgentHistoryResponse(BaseModel):
    agentId: str
    sessionId: str
    messages: list[AgentHistoryMessage]
