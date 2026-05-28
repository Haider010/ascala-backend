from typing import Any, Literal, TypedDict


class EscouadeState(TypedDict, total=False):
    action: Literal["generate", "revise", "approve", "reopen", "export", "fetch"]
    location_id: str
    batch_id: str
    member_type: str
    filters: dict[str, Any]
    batch_name: str
    source_type: str
    source_label: str
    conversation_history: list[dict[str, Any]]
    current_instruction: str
    item_ids: list[str]
    editable_item_ids: list[str]
    locked_item_ids: list[str]
    items: list[dict[str, Any]]
    generated_items: list[dict[str, Any]]
    knowledge_context: dict[str, Any]
    production_brief: dict[str, Any]
    strategy_review: dict[str, Any]
    quality_note: str | None
    message: str
    csv_content: str
    csv_filename: str
