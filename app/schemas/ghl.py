from pydantic import BaseModel, Field

from app.schemas.agents import AgentHistoryResponse, AgentWorkflowStatus


class GhlSessionRequest(BaseModel):
    encryptedData: str
    queryParams: dict = Field(default_factory=dict)


class GhlSessionResponse(BaseModel):
    sessionToken: str
    userId: str | None = None
    companyId: str | None = None
    activeLocation: str | None = None
    role: str | None = None
    type: str | None = None
    userName: str | None = None
    email: str | None = None
    isAgencyOwner: bool | None = None
    storageScope: str
    histories: list[AgentHistoryResponse] = Field(default_factory=list)
    workflowStatus: AgentWorkflowStatus | None = None
