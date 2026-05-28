from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

MemberType = Literal["carrousel", "reel", "image_post", "stories", "text_post"]
ItemStatus = Literal["draft", "needs_revision", "approved", "exported"]


class EscouadeBatchFilters(BaseModel):
    source_type: str | None = None
    source_label: str | None = None
    platforms: list[str] = Field(default_factory=list)
    primary_platform: str | None = None
    objective: str | None = None
    content_style: list[str] = Field(default_factory=list)
    quantity: int = Field(default=5, ge=1, le=50)
    cta_preference: str | None = None
    language: str | None = None
    interaction_style: str | None = None
    reference_mode: list[str] = Field(default_factory=list)
    special_instructions: str | None = None
    format_filters: dict[str, Any] = Field(default_factory=dict)


class BatchGenerateRequest(BaseModel):
    member_type: MemberType
    batch_name: str | None = None
    source_type: str | None = None
    source_label: str | None = None
    filters: EscouadeBatchFilters = Field(default_factory=EscouadeBatchFilters)
    message: str
    conversation_history: list[dict[str, Any]] = Field(default_factory=list)


class ReviseRequest(BaseModel):
    batch_id: UUID
    item_ids: list[UUID]
    instruction: str
    conversation_history: list[dict[str, Any]] = Field(default_factory=list)


class ItemActionRequest(BaseModel):
    batch_id: UUID
    item_ids: list[UUID]


class CommandRequest(BaseModel):
    batch_id: UUID
    message: str
    conversation_history: list[dict[str, Any]] = Field(default_factory=list)


class EscouadeItemResponse(BaseModel):
    id: UUID
    batch_id: UUID
    post_id: str
    member_type: str
    content: dict[str, Any]
    status: str
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class BatchResponse(BaseModel):
    id: UUID
    location_id: str
    member_type: str
    batch_name: str | None = None
    source_type: str | None = None
    source_label: str | None = None
    filters: dict[str, Any]
    status: str
    strategy_review: dict[str, Any] = Field(default_factory=dict)
    quality_note: str | None = None
    dashboard: dict[str, Any] = Field(default_factory=dict)
    items: list[EscouadeItemResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class EscouadeOperationResponse(BaseModel):
    batch: BatchResponse
    message: str
    workflowStatus: dict[str, Any] | None = None
