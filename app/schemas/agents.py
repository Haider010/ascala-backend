from pydantic import BaseModel


class AgentWorkflowStep(BaseModel):
    id: str
    name: str
    role: str
    status: str
    locked: bool
    completed: bool
    available: bool


class AgentWorkflowStatus(BaseModel):
    currentAgentId: str
    steps: list[AgentWorkflowStep]


class AgentChatRequest(BaseModel):
    agentId: str
    message: str
    sessionId: str | None = None


class AgentChatResponse(BaseModel):
    payload: object
    sessionId: str
    workflowStatus: AgentWorkflowStatus | None = None


class AgentHistoryMessage(BaseModel):
    id: str
    role: str
    content: str
    createdAt: str | None = None


class AgentHistoryResponse(BaseModel):
    agentId: str
    sessionId: str
    messages: list[AgentHistoryMessage]
