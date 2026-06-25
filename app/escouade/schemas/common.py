from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

MemberType = Literal["carrousel", "reel", "image_post", "stories", "text_post"]
ItemStatus = Literal["draft", "needs_revision", "revised", "approved", "exported"]


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
    output_target: str | None = None
    production_columns: list[dict[str, Any]] = Field(default_factory=list)
    fixed_fields: dict[str, Any] = Field(default_factory=dict)
    variable_fields: list[str] = Field(default_factory=list)
    media_placeholders: list[str] = Field(default_factory=list)


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


class SetupParseRequest(BaseModel):
    instruction: str
    current_setup: dict[str, Any] = Field(default_factory=dict)
    conversation_history: list[dict[str, Any]] = Field(default_factory=list)


class SetupUpdateResponse(BaseModel):
    updates: dict[str, Any] = Field(default_factory=dict)
    updated_fields: list[str] = Field(default_factory=list)
    matched_brief_id: str | None = None
    matched_brief_title: str | None = None
    matched_brief: dict[str, Any] | None = None
    message: str = "Setup updated."


class EscouadeChatMessageCreateRequest(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class EscouadeChatMessageResponse(BaseModel):
    id: UUID
    location_id: str
    role: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    model_config = {"from_attributes": True}


class EscouadeChatThreadResponse(BaseModel):
    messages: list[EscouadeChatMessageResponse] = Field(default_factory=list)


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


class EscouadeProductionTableResponse(BaseModel):
    id: UUID | None = None
    columns: list[dict[str, Any]] = Field(default_factory=list)
    export_format: str = "csv"
    version: int = 1
    rows: list[dict[str, Any]] = Field(default_factory=list)


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
    production_table: EscouadeProductionTableResponse | None = None
    items: list[EscouadeItemResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class EscouadeOperationResponse(BaseModel):
    batch: BatchResponse
    message: str
    workflowStatus: dict[str, Any] | None = None
