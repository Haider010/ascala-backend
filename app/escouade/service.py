import json
import re
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload

from app.agents.common import read_platform_knowledge
from app.core.config import get_settings
from app.escouade.csv import build_items_csv, build_production_table_csv, flatten_item
from app.escouade.models import EscouadeBatch, EscouadeChatMessage, EscouadeItem, EscouadeProductionRow, EscouadeProductionTable
from app.escouade.schemas.common import EscouadeBatchFilters
from app.escouade.schemas.member_outputs import MEMBER_ITEM_SCHEMAS, MEMBER_OUTPUT_SCHEMAS
from app.services.escouade_brief import extract_escouade_brief, extract_escouade_production_menu
from app.services.token_usage import record_token_usage

PROMPT_DIR = Path(__file__).with_name("prompts")
EDITABLE_STATUSES = {"draft", "needs_revision", "revised"}
LOCKED_STATUSES = {"approved", "exported"}


class SetupParserOutput(BaseModel):
    matched_brief_id: str | None = None
    member_type: str | None = None
    batch_name: str | None = None
    source_type: str | None = None
    source_label: str | None = None
    quantity: int | None = Field(default=None, ge=1, le=50)
    primary_platform: str | None = None
    objective: str | None = None
    cta_preference: str | None = None
    language: str | None = None
    content_style: str | None = None
    interaction_style: str | None = None
    output_target: str | None = None
    production_columns: list[dict[str, Any]] = Field(default_factory=list)
    fixed_fields: dict[str, Any] = Field(default_factory=dict)
    variable_fields: list[str] = Field(default_factory=list)
    media_placeholders: list[str] = Field(default_factory=list)
    special_instructions: str | None = None
    format_filters: dict[str, Any] = Field(default_factory=dict)
    updated_fields: list[str] = Field(default_factory=list)
    message: str = "Setup updated."


def read_prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")


def require_location_id(session_context: dict) -> str:
    location_id = session_context.get("activeLocation") or session_context.get("locationId")
    if not location_id:
        raise HTTPException(status_code=400, detail="Unable to determine location context.")
    return location_id


def production_column(key: str, label: str, column_type: str = "text", required: bool = False, locked: bool = False) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "type": column_type,
        "required": required,
        "locked": locked,
    }


def parse_first_int(value: Any, default: int) -> int:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group(0)) if match else default


def default_production_columns(member_type: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    filters = filters or {}
    format_filters = filters.get("format_filters") or {}
    base = [
        production_column("post_id", "Post ID", "metadata", required=True, locked=True),
        production_column("platform", "Platform", "metadata", required=True),
        production_column("objective", "Objective", "metadata"),
        production_column("content_angle", "Content Angle", "metadata"),
    ]

    if member_type == "carrousel":
        slide_count = parse_first_int(format_filters.get("slide_structure"), 7)
        columns = base + [
            production_column("media", "Media", "media_placeholder"),
            production_column("cover_headline", "Cover Headline", required=True),
            production_column("cover_visual_direction", "Cover Visual Direction", "long_text"),
        ]
        for number in range(1, min(max(slide_count, 2), 10) + 1):
            columns.extend([
                production_column(f"slide_{number}_role", f"Slide {number} Role"),
                production_column(f"slide_{number}_headline", f"Slide {number} Headline", required=True),
                production_column(f"slide_{number}_body", f"Slide {number} Body", "long_text", required=True),
                production_column(f"slide_{number}_visual_direction", f"Slide {number} Visual Direction", "long_text"),
            ])
        columns.extend([
            production_column("caption", "Caption", "long_text"),
            production_column("cta", "CTA", "cta"),
            production_column("hashtags", "Hashtags", "hashtags"),
        ])
        return columns

    if member_type == "reel":
        field_count = parse_first_int(format_filters.get("field_count"), 5)
        columns = base + [
            production_column("video", "Video", "media_placeholder"),
            production_column("hook", "Hook", required=True),
            production_column("opening_visual", "Opening Visual"),
        ]
        for number in range(1, min(max(field_count, 3), 10) + 1):
            columns.append(production_column(f"field_{number}", f"Field {number}", "long_text"))
        columns.extend([
            production_column("script", "Script", "long_text"),
            production_column("on_screen_text", "On-Screen Text", "long_text"),
            production_column("b_roll_direction", "B-roll Direction", "long_text"),
            production_column("caption", "Caption", "long_text"),
            production_column("cta", "CTA", "cta"),
            production_column("hashtags", "Hashtags", "hashtags"),
        ])
        return columns

    if member_type == "stories":
        slide_count = parse_first_int(format_filters.get("story_sequence_length"), 5)
        columns = base + [
            production_column("media", "Media", "media_placeholder"),
            production_column("sequence_name", "Sequence Name", required=True),
            production_column("story_flow", "Story Flow"),
        ]
        for number in range(1, min(max(slide_count, 2), 5) + 1):
            columns.extend([
                production_column(f"slide_{number}_purpose", f"Story {number} Purpose"),
                production_column(f"slide_{number}_text", f"Story {number} Text", "long_text", required=True),
                production_column(f"slide_{number}_interaction", f"Story {number} Interaction"),
                production_column(f"slide_{number}_visual_direction", f"Story {number} Visual Direction", "long_text"),
            ])
        columns.extend([
            production_column("final_cta", "Final CTA", "cta"),
            production_column("cta", "CTA", "cta"),
        ])
        return columns

    if member_type == "text_post":
        return base + [
            production_column("post_type", "Post Type"),
            production_column("hook", "Hook", required=True),
            production_column("body", "Body", "long_text", required=True),
            production_column("closing_line", "Closing Line"),
            production_column("cta", "CTA", "cta"),
            production_column("hashtags", "Hashtags", "hashtags"),
        ]

    return base + [
        production_column("image", "Image", "media_placeholder"),
        production_column("image_post_type", "Image Post Type"),
        production_column("overlay_text", "Overlay Text", required=True),
        production_column("supporting_line", "Supporting Line"),
        production_column("visual_direction", "Visual Direction", "long_text"),
        production_column("caption", "Caption", "long_text"),
        production_column("cta", "CTA", "cta"),
        production_column("hashtags", "Hashtags", "hashtags"),
    ]


def normalize_column_key(value: Any) -> str:
    key = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return key or "column"


def normalize_production_columns(generated_columns: Any, member_type: str, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    defaults = default_production_columns(member_type, filters)
    if not isinstance(generated_columns, list) or not generated_columns:
        return defaults

    normalized = []
    seen = set()
    for column in generated_columns:
        if hasattr(column, "model_dump"):
            column = column.model_dump()
        if not isinstance(column, dict):
            continue
        key = normalize_column_key(column.get("key") or column.get("label"))
        if key in seen:
            continue
        seen.add(key)
        normalized.append({
            "key": key,
            "label": str(column.get("label") or key.replace("_", " ").title()),
            "type": str(column.get("type") or "text"),
            "required": bool(column.get("required", False)),
            "locked": bool(column.get("locked", False)),
        })

    default_by_key = {column["key"]: column for column in defaults}
    for required_key in ("post_id", "platform", "objective"):
        if required_key not in seen and required_key in default_by_key:
            normalized.insert(0, default_by_key[required_key])
            seen.add(required_key)

    return normalized or defaults


def setup_production_columns(filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    filters = filters or {}
    columns = filters.get("production_columns")
    if not isinstance(columns, list) or not columns:
        return []

    normalized = []
    seen = set()
    for column in columns:
        if not isinstance(column, dict):
            column = {"label": str(column)}
        key = normalize_column_key(column.get("key") or column.get("label"))
        if key in seen:
            continue
        seen.add(key)
        normalized.append({
            "key": key,
            "label": str(column.get("label") or key.replace("_", " ").title()),
            "type": str(column.get("type") or "text"),
            "required": bool(column.get("required", False)),
            "locked": bool(column.get("locked", False)),
        })
    return normalized


def csv_ready_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(item) if not isinstance(item, dict) else json.dumps(item, ensure_ascii=False) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def is_real_media_url(value: Any) -> bool:
    return bool(re.match(r"^https?://", str(value or "").strip(), re.IGNORECASE))


def is_media_placeholder_column(column: dict[str, Any], filters: dict[str, Any] | None = None) -> bool:
    filters = filters or {}
    key = normalize_column_key(column.get("key") or column.get("label"))
    label = normalize_column_key(column.get("label") or key)
    placeholder_keys = {
        normalize_column_key(value)
        for value in filters.get("media_placeholders", [])
        if value not in (None, "")
    } if isinstance(filters.get("media_placeholders"), list) else set()

    if key in placeholder_keys or label in placeholder_keys:
        return True
    if str(column.get("type") or "").lower() == "media_placeholder":
        return True
    return any(token in key for token in ("image", "video", "media", "asset", "file", "upload"))


def derive_table_row(member_type: str, content: dict[str, Any], columns: list[dict[str, Any]]) -> dict[str, str]:
    flattened = flatten_item(member_type, content)
    generated_row = content.get("table_row") if isinstance(content.get("table_row"), dict) else {}

    if member_type == "reel":
        for number, field in enumerate(content.get("on_screen_text") or [], start=1):
            flattened[f"field_{number}"] = field

    row = {}
    for column in columns:
        key = column.get("key")
        if not key:
            continue
        value = generated_row.get(key, flattened.get(key))
        if value is None and column.get("type") == "media_placeholder":
            value = ""
        row[key] = csv_ready_value(value)
    return row


def enforce_setup_row_rules(
    row: dict[str, str],
    columns: list[dict[str, Any]],
    filters: dict[str, Any] | None = None,
) -> dict[str, str]:
    filters = filters or {}
    fixed_fields = filters.get("fixed_fields") if isinstance(filters.get("fixed_fields"), dict) else {}
    normalized_fixed = {
        normalize_column_key(key): value
        for key, value in fixed_fields.items()
    }

    exact_row: dict[str, str] = {}
    for column in columns:
        key = column.get("key")
        if not key:
            continue

        value = row.get(key, "")
        normalized_key = normalize_column_key(key)
        if normalized_key in normalized_fixed:
            value = normalized_fixed[normalized_key]
        elif is_media_placeholder_column(column, filters) and not is_real_media_url(value):
            value = ""

        exact_row[key] = csv_ready_value(value)

    return exact_row


def apply_fixed_and_media_fields(row: dict[str, str], filters: dict[str, Any] | None = None) -> dict[str, str]:
    columns = [{"key": key, "label": key, "type": "text"} for key in row.keys()]
    return enforce_setup_row_rules(row, columns, filters)


def row_from_content(member_type: str, content: dict[str, Any], columns: list[dict[str, Any]], filters: dict[str, Any] | None = None) -> dict[str, str]:
    return enforce_setup_row_rules(derive_table_row(member_type, content, columns), columns, filters)


def variable_rule_violations(rows: list[dict[str, str]], filters: dict[str, Any] | None = None) -> list[str]:
    filters = filters or {}
    variable_fields = filters.get("variable_fields") if isinstance(filters.get("variable_fields"), list) else []
    violations = []
    if len(rows) < 2:
        return violations

    for field in variable_fields:
        key = normalize_column_key(field)
        values = [str(row.get(key, "")).strip() for row in rows if str(row.get(key, "")).strip()]
        if len(values) > 1 and len(set(values)) == 1:
            violations.append(str(field))
    return violations


def validated_contents_and_rows(
    member_type: str,
    generated: Any,
    item_schema: type[BaseModel],
    columns: list[dict[str, Any]],
    filters: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    contents = []
    rows = []
    for generated_item in generated.items:
        content = item_schema.model_validate(generated_item).model_dump()
        contents.append(content)
        rows.append(row_from_content(member_type, content, columns, filters or {}))
    return contents, rows


def output_export_format(filters: dict[str, Any] | None = None) -> str:
    target = normalize_column_key((filters or {}).get("output_target") or "csv")
    if "canva" in target:
        return "canva_bulk_create_csv"
    if "b10x" in target or "social_planner" in target:
        return "b10x_social_planner_csv"
    if "custom" in target:
        return "custom_csv"
    return "csv"


def export_format_label(export_format: str) -> str:
    return {
        "canva_bulk_create_csv": "Canva Bulk Create CSV",
        "b10x_social_planner_csv": "B10X Social Planner prep CSV",
        "custom_csv": "Custom CSV",
        "csv": "Escouade structured CSV",
    }.get(export_format, "Escouade structured CSV")


def export_format_slug(export_format: str) -> str:
    return {
        "canva_bulk_create_csv": "canva-bulk-create",
        "b10x_social_planner_csv": "b10x-social-planner-prep",
        "custom_csv": "custom",
        "csv": "structured",
    }.get(export_format, "structured")


def output_target_rules(filters: dict[str, Any] | None = None) -> dict[str, Any]:
    target = str((filters or {}).get("output_target") or "B10X Social Planner")
    normalized = normalize_column_key(target)
    if "canva" in normalized:
        return {
            "target": target,
            "export_format": "canva_bulk_create_csv",
            "rules": [
                "Treat production_columns as Canva Bulk Create merge fields.",
                "Use short, clean field values that can fit inside design text boxes.",
                "Keep media placeholder columns empty unless a real uploaded media URL exists.",
                "Do not add scheduling metadata unless the user requested it as a column.",
            ],
        }
    if "b10x" in normalized or "social_planner" in normalized:
        return {
            "target": target,
            "export_format": "b10x_social_planner_csv",
            "rules": [
                "Prepare rows for centralized scheduling through B10X Social Planner.",
                "Captions, hashtags, CTA, and media placeholders should be CSV-ready.",
                "Do not tell the user to post manually or schedule separately in each social app.",
                "Use one strong core post with light platform adaptation when useful.",
            ],
        }
    return {
        "target": target,
        "export_format": output_export_format(filters),
        "rules": [
            "Prepare clean CSV-ready values for the requested production table.",
            "Use exact columns when provided.",
            "Keep media placeholder columns empty unless a real URL exists.",
        ],
    }


def member_quality_profile(member_type: str, filters: dict[str, Any] | None = None) -> dict[str, Any]:
    filters = filters or {}
    format_filters = filters.get("format_filters") or {}
    quantity = filters.get("quantity")
    objective = filters.get("objective")
    cta = filters.get("cta_preference")

    profiles: dict[str, dict[str, Any]] = {
        "image_post": {
            "job": "Create static posts with one strong visual message and one supporting caption.",
            "table_behavior": [
                "Use image/asset columns as empty media placeholders unless a real URL is supplied.",
                "Overlay text must fit on a visual; keep it short enough for a design template.",
                "Supporting line should add context without turning the image into a mini article.",
                "Caption should carry the explanation, proof, example, or CTA.",
            ],
            "quality_checks": [
                "One idea per post.",
                "Overlay text is scan-friendly.",
                "Visual direction is concrete enough for a designer or template.",
                "Caption supports the selected objective.",
            ],
        },
        "carrousel": {
            "job": "Create slide-by-slide carousel posts with a clear progression from hook to value to action.",
            "table_behavior": [
                "Respect the requested slide count from format filters.",
                "Every slide column should have a clear role, headline, body, and visual direction.",
                "Cover headline must work as the scroll-stopper.",
                "Caption should summarize the carousel and extend the CTA when relevant.",
            ],
            "quality_checks": [
                "One job per slide.",
                "No overloaded slides.",
                "Slide sequence has a beginning, middle, and payoff.",
                "The final slide or caption handles CTA preference cleanly.",
            ],
        },
        "text_post": {
            "job": "Create text-first posts that can stand alone without a graphic.",
            "table_behavior": [
                "Hook should be strong enough to open the post.",
                "Body should be structured with readable paragraphs or bullets when useful.",
                "Closing line should feel intentional, not generic.",
                "Hashtags should be optional and restrained.",
            ],
            "quality_checks": [
                "The post has a clear argument, story, lesson, or point of view.",
                "It avoids empty thought-leadership language.",
                "It sounds human and aligned with the brand voice.",
                "CTA matches the selected CTA preference.",
            ],
        },
        "stories": {
            "job": "Create tap-through story sequences with light interaction and a clear sequence flow.",
            "table_behavior": [
                "Respect the requested story slide count.",
                "Each story slide needs purpose, text, interaction when useful, and visual direction.",
                "Interactions should be practical: poll, question, quiz, slider, DM prompt, or link instruction.",
                "Final CTA should be soft and native to stories unless the user asked for a direct CTA.",
            ],
            "quality_checks": [
                "Each slide is short enough for story consumption.",
                "The sequence feels conversational.",
                "Interactions are not forced.",
                "The final slide resolves the flow.",
            ],
        },
        "reel": {
            "job": "Create short-form video scripts with flexible fields rather than rigid scenes.",
            "table_behavior": [
                "Respect requested field count, target length, voiceover, and reel type.",
                "Field columns should map to creator-ready beats.",
                "Hook, opening visual, script, on-screen text, B-roll, caption, and CTA should work together.",
                "Video/media columns stay empty unless a real uploaded URL exists.",
            ],
            "quality_checks": [
                "Hook is immediate and specific.",
                "Script can be spoken naturally.",
                "On-screen text beats are short.",
                "B-roll direction is realistic for the user to produce.",
            ],
        },
    }

    return {
        "member_type": member_type,
        "quantity": quantity,
        "objective": objective,
        "cta_preference": cta,
        "format_filters": format_filters,
        **profiles.get(member_type, profiles["image_post"]),
    }


def serialize_production_table(batch: EscouadeBatch) -> dict[str, Any] | None:
    table = batch.production_table
    if not table:
        return None

    rows_by_item_id = {str(row.item_id): row for row in batch.production_rows}
    rows = []
    for item in batch.items:
        row = rows_by_item_id.get(str(item.id))
        if not row:
            continue
        rows.append({
            "id": str(row.id),
            "item_id": str(row.item_id),
            "post_id": row.post_id,
            "status": item.status,
            "data": row.row_data,
            "version": row.version,
        })

    return {
        "id": table.id,
        "columns": table.columns or [],
        "export_format": table.export_format,
        "version": table.version,
        "rows": rows,
    }


def serialize_batch(batch: EscouadeBatch) -> dict:
    dashboard = build_batch_dashboard(batch)
    return {
        "id": batch.id,
        "location_id": batch.location_id,
        "member_type": batch.member_type,
        "batch_name": batch.batch_name,
        "source_type": batch.source_type,
        "source_label": batch.source_label,
        "filters": batch.filters,
        "status": batch.status,
        "strategy_review": batch.strategy_review or {},
        "quality_note": batch.quality_note,
        "dashboard": dashboard,
        "production_table": serialize_production_table(batch),
        "created_at": batch.created_at,
        "updated_at": batch.updated_at,
        "items": [
            {
                "id": item.id,
                "batch_id": item.batch_id,
                "post_id": item.post_id,
                "member_type": item.member_type,
                "content": item.content,
                "status": item.status,
                "version": item.version,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
            }
            for item in batch.items
        ],
    }


def preview_item(item: EscouadeItem) -> dict[str, Any]:
    content = item.content or {}
    member_type = item.member_type

    if member_type == "carrousel":
        slides = content.get("slides") or []
        title = content.get("cover_headline") or item.post_id
        description = f"{len(slides)} slides"
        if content.get("strategic_note"):
            description = f"{description} · {content['strategic_note'][:80]}"
    elif member_type == "reel":
        title = content.get("hook") or item.post_id
        field_count = len(content.get("on_screen_text") or [])
        description = f"{field_count or 'Flexible'} fields · {content.get('objective', '')}".strip(" ·")
    elif member_type == "image_post":
        title = content.get("overlay_text") or item.post_id
        description = f"{content.get('content_style', 'Image post')} · {content.get('objective', '')}".strip(" ·")
    elif member_type == "stories":
        slides = content.get("slides") or []
        title = slides[0].get("text") if slides and isinstance(slides[0], dict) else content.get("sequence_name") or item.post_id
        description = f"{len(slides)} story slides"
    else:
        title = content.get("hook") or content.get("body", item.post_id)[:100]
        description = f"{content.get('platform', '')} · {content.get('objective', '')}".strip(" ·")

    return {
        "id": str(item.id),
        "post_id": item.post_id,
        "title": title,
        "description": description,
        "status": item.status,
    }


def build_batch_dashboard(batch: EscouadeBatch) -> dict[str, Any]:
    counts = {"draft": 0, "needs_revision": 0, "revised": 0, "approved": 0, "exported": 0}
    for item in batch.items:
        counts[item.status] = counts.get(item.status, 0) + 1

    active_items = [item for item in batch.items if item.status != "exported"]
    approved_items = [item for item in batch.items if item.status == "approved"]

    return {
        "counts": counts,
        "approved_count": counts.get("approved", 0),
        "total_count": len(batch.items),
        "active_items": [preview_item(item) for item in active_items],
        "approved_items": [preview_item(item) for item in approved_items],
        "batch_context": {
            "member_type": batch.member_type,
            "batch_name": batch.batch_name,
            "source_type": batch.source_type,
            "source_label": batch.source_label,
            "platforms": (batch.filters or {}).get("platforms", []),
            "objective": (batch.filters or {}).get("objective"),
            "language": (batch.filters or {}).get("language"),
            "cta_preference": (batch.filters or {}).get("cta_preference"),
            "content_style": (batch.filters or {}).get("content_style", []),
        },
    }


def list_chat_messages(
    db: Session,
    location_id: str,
) -> list[EscouadeChatMessage]:
    query = select(EscouadeChatMessage).where(EscouadeChatMessage.location_id == location_id)
    return list(db.scalars(query.order_by(EscouadeChatMessage.created_at.asc(), EscouadeChatMessage.id.asc())).all())


def serialize_chat_message(message: EscouadeChatMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "location_id": message.location_id,
        "role": message.role,
        "content": message.content,
        "metadata": message.message_metadata or {},
        "created_at": message.created_at,
    }


def create_chat_message(
    db: Session,
    location_id: str,
    role: str,
    content: str,
    metadata: dict[str, Any] | None = None,
) -> EscouadeChatMessage:
    content = (content or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="Chat message content is required.")

    message = EscouadeChatMessage(
        location_id=location_id,
        role=role,
        content=content,
        message_metadata=metadata or {},
    )
    db.add(message)
    db.flush()
    db.refresh(message)
    return message


def get_batch_or_404(db: Session, batch_id: UUID | str, location_id: str) -> EscouadeBatch:
    batch = db.scalars(
        select(EscouadeBatch)
        .options(
            selectinload(EscouadeBatch.items),
            selectinload(EscouadeBatch.production_table),
            selectinload(EscouadeBatch.production_rows),
        )
        .where(EscouadeBatch.id == batch_id, EscouadeBatch.location_id == location_id)
    ).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Escouade batch not found.")
    return batch


def get_latest_batch(db: Session, location_id: str) -> EscouadeBatch | None:
    base_query = (
        select(EscouadeBatch)
        .options(
            selectinload(EscouadeBatch.items),
            selectinload(EscouadeBatch.production_table),
            selectinload(EscouadeBatch.production_rows),
        )
        .where(EscouadeBatch.location_id == location_id)
        .order_by(EscouadeBatch.updated_at.desc(), EscouadeBatch.created_at.desc())
    )
    active_batch = db.scalars(base_query.where(EscouadeBatch.status != "exported")).first()
    if active_batch:
        return active_batch
    return db.scalars(base_query).first()


def list_batches(db: Session, location_id: str, limit: int = 30) -> list[EscouadeBatch]:
    return list(db.scalars(
        select(EscouadeBatch)
        .options(
            selectinload(EscouadeBatch.items),
            selectinload(EscouadeBatch.production_table),
            selectinload(EscouadeBatch.production_rows),
        )
        .where(EscouadeBatch.location_id == location_id)
        .order_by(EscouadeBatch.updated_at.desc(), EscouadeBatch.created_at.desc())
        .limit(max(1, min(limit, 100)))
    ).all())


def load_knowledge_context(db: Session, location_id: str) -> dict[str, Any]:
    context = {}
    for agent_id, table_name in (
        ("molly", "molly_outputs"),
        ("brandy", "brandy_outputs"),
        ("brandboard", "brandboard_outputs"),
        ("sacha", "sacha_outputs"),
    ):
        row = db.execute(
            text(f"""
                SELECT final_output, structured_output, output_type, version, updated_at
                FROM {table_name}
                WHERE location_id = :location_id
                LIMIT 1
            """),
            {"location_id": location_id},
        ).mappings().first()

        if not row:
            context[agent_id] = None
            continue

        structured_output = row.get("structured_output") or {}
        context[agent_id] = {
            "output_type": row.get("output_type"),
            "version": row.get("version"),
            "updated_at": str(row.get("updated_at")) if row.get("updated_at") else None,
            "summary": build_compact_summary(row.get("final_output") or "", structured_output),
        }

    return context


def load_sacha_production_brief(db: Session, location_id: str) -> dict[str, Any]:
    row = db.execute(
        text("""
            SELECT final_output, structured_output, updated_at
            FROM sacha_outputs
            WHERE location_id = :location_id
            LIMIT 1
        """),
        {"location_id": location_id},
    ).mappings().first()

    if not row:
        return {"brief": None, "updatedAt": None, "source": "sacha"}

    structured_output = row.get("structured_output") or {}
    brief = structured_output.get("escouade_brief") if isinstance(structured_output, dict) else None
    menu = structured_output.get("escouade_production_menu") if isinstance(structured_output, dict) else None
    if not brief:
        brief = extract_escouade_brief(row.get("final_output") or "")
    if not menu:
        menu = extract_escouade_production_menu(row.get("final_output") or "")

    return {
        "brief": brief or None,
        "menu": menu or [],
        "updatedAt": str(row.get("updated_at")) if row.get("updated_at") else None,
        "source": "sacha",
    }


def build_compact_summary(final_output: str, structured_output: dict) -> dict[str, Any]:
    sections = structured_output.get("sections") if isinstance(structured_output, dict) else None
    if not isinstance(sections, dict) and isinstance(structured_output, dict):
        sections = structured_output
    if isinstance(sections, dict) and sections:
        compact_sections = {}
        for key, value in list(sections.items())[:16]:
            if isinstance(value, dict):
                content = str(value.get("content", "")) or json.dumps(value, ensure_ascii=False, default=str)
            else:
                content = str(value)
            compact_sections[key] = content[:1200]
        return {"sections": compact_sections}

    return {"document_excerpt": final_output[:6000]}


def pick_sections(agent_context: dict | None, desired_keys: list[str]) -> dict[str, str]:
    if not agent_context:
        return {}

    summary = agent_context.get("summary") or {}
    sections = summary.get("sections") or {}
    if not isinstance(sections, dict):
        return {"excerpt": str(summary.get("document_excerpt", ""))[:1800]}

    picked = {}
    for desired_key in desired_keys:
        for key, value in sections.items():
            if desired_key in key:
                picked[key] = str(value)[:1500]
                break
    return picked


def build_production_brief(knowledge_context: dict[str, Any], member_type: str, filters: dict[str, Any]) -> dict[str, Any]:
    return {
        "member_type": member_type,
        "filters": filters,
        "audience_context": pick_sections(
            knowledge_context.get("molly"),
            ["positioning", "ideal_client", "pain", "buyer", "language", "content_engine", "hook"],
        ),
        "brand_voice_context": pick_sections(
            knowledge_context.get("brandy"),
            ["brand_summary", "voice", "tone", "language", "guardrails", "audience"],
        ),
        "brandboard_context": pick_sections(
            knowledge_context.get("brandboard"),
            [
                "brand_foundation",
                "audience_messaging",
                "voice_tone_system",
                "content_application_system",
                "color_system",
                "typography_system",
                "button_system",
            ],
        ),
        "strategy_context": pick_sections(
            knowledge_context.get("sacha"),
            ["theme", "pillar", "series", "calendar", "cta", "platform", "production"],
        ),
    }


def build_strategy_review(member_type: str, filters: dict[str, Any], production_brief: dict[str, Any]) -> dict[str, Any]:
    notes = []
    recommendations = []
    objective = (filters.get("objective") or "").lower()
    cta_preference = (filters.get("cta_preference") or "").lower()
    content_styles = " ".join(filters.get("content_style") or []).lower()
    format_filters = filters.get("format_filters") or {}

    if "lead" in objective and ("no cta" in cta_preference or not cta_preference):
        notes.append("Lead generation is selected, but CTA preference is weak or empty.")
        recommendations.append("Use at least a soft CTA on selected items so the batch supports conversion.")

    if "premium" in content_styles:
        recommendations.append("Keep language polished, authority-led, and specific. Avoid hype or casual filler.")

    if member_type == "carrousel" and not format_filters.get("slide_structure"):
        recommendations.append("Use a 7-slide structure by default for enough depth without making the carousel heavy.")
    if member_type == "image_post" and "authority" in objective:
        recommendations.append("Favor bold insights or educational statements over generic quotes.")
    if member_type == "reel":
        recommendations.append("Use flexible hook/script fields instead of rigid scenes.")

    if not notes:
        notes.append("The batch setup is coherent enough to generate.")

    return {
        "summary": " ".join(notes),
        "recommendations": recommendations[:5],
        "objective_alignment": filters.get("objective"),
        "member_fit": member_type,
    }


def build_llm():
    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not configured.")
    return ChatOpenAI(
        model=settings.escouade_model,
        api_key=settings.openai_api_key,
        temperature=0.6,
        timeout=settings.agent_timeout_seconds,
    )


def infer_column_type(label: str) -> str:
    normalized = normalize_column_key(label)
    if any(token in normalized for token in ("image", "video", "media", "asset", "file", "upload")):
        return "media_placeholder"
    if "cta" in normalized:
        return "cta"
    if "hashtag" in normalized:
        return "hashtags"
    if any(token in normalized for token in ("caption", "body", "script", "description")):
        return "long_text"
    return "text"


def normalize_setup_update_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    cleaned = []
    seen = set()
    for value in values:
        text_value = str(value or "").strip()
        normalized = normalize_column_key(text_value)
        if not text_value or normalized in seen:
            continue
        cleaned.append(text_value)
        seen.add(normalized)
    return cleaned


def normalize_setup_parser_updates(parsed: SetupParserOutput) -> dict[str, Any]:
    raw = parsed.model_dump()
    updates: dict[str, Any] = {}
    scalar_fields = (
        "member_type",
        "batch_name",
        "source_type",
        "source_label",
        "quantity",
        "primary_platform",
        "objective",
        "cta_preference",
        "language",
        "content_style",
        "interaction_style",
        "output_target",
        "special_instructions",
    )

    for field in scalar_fields:
        value = raw.get(field)
        if value is not None and value != "":
            updates[field] = value

    production_columns = raw.get("production_columns")
    if isinstance(production_columns, list) and production_columns:
        normalized_columns = []
        for column in production_columns:
            if not isinstance(column, dict):
                column = {"label": str(column)}
            label = str(column.get("label") or column.get("key") or "").strip()
            if not label:
                continue
            normalized_columns.append({
                **column,
                "label": label,
                "key": column.get("key") or normalize_column_key(label),
                "type": column.get("type") or infer_column_type(label),
            })
        updates["production_columns"] = setup_production_columns({"production_columns": normalized_columns})

    fixed_fields = raw.get("fixed_fields")
    if isinstance(fixed_fields, dict) and fixed_fields:
        updates["fixed_fields"] = {
            str(key).strip(): value
            for key, value in fixed_fields.items()
            if str(key).strip()
        }

    variable_fields = normalize_setup_update_list(raw.get("variable_fields"))
    if variable_fields:
        updates["variable_fields"] = variable_fields

    media_placeholders = normalize_setup_update_list(raw.get("media_placeholders"))
    if media_placeholders:
        updates["media_placeholders"] = media_placeholders

    format_filters = raw.get("format_filters")
    if isinstance(format_filters, dict) and format_filters:
        updates["format_filters"] = {
            str(key).strip(): value
            for key, value in format_filters.items()
            if str(key).strip() and value not in (None, "")
        }

    return updates


def menu_match_text(item: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "id",
        "title",
        "type",
        "source_label",
        "objective",
        "primary_platform",
        "cta_direction",
        "angle",
        "production_notes",
    ):
        value = item.get(key)
        if value:
            parts.append(str(value))
    for value in item.get("recommended_formats") or []:
        parts.append(str(value))
    for value in item.get("platforms") or []:
        parts.append(str(value))
    brief = item.get("brief") if isinstance(item.get("brief"), dict) else {}
    if brief:
        parts.extend(str(brief.get(key)) for key in ("batch_name", "source_label", "message") if brief.get(key))
        filters = brief.get("filters") if isinstance(brief.get("filters"), dict) else {}
        parts.extend(str(filters.get(key)) for key in ("source_label", "objective", "special_instructions") if filters.get(key))
    return " ".join(parts)


def normalize_match_query(value: Any) -> str:
    text_value = str(value or "").lower()
    text_value = re.sub(r"\b(use|using|the|a|an|series|brief|batch|from|for|please|make|create|with)\b", " ", text_value)
    return re.sub(r"[^a-z0-9]+", " ", text_value).strip()


def match_sacha_menu_item(instruction: str, production_menu: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    query = normalize_match_query(instruction)
    if not query or not production_menu:
        return None

    query_tokens = {token for token in query.split() if len(token) > 1}
    best_item = None
    best_score = 0.0

    for item in production_menu:
        haystack = normalize_match_query(menu_match_text(item))
        if not haystack:
            continue

        score = 0.0
        if query and query in haystack:
            score += 5.0
        title = normalize_match_query(item.get("title"))
        source_label = normalize_match_query(item.get("source_label"))
        if title and (title in query or query in title):
            score += 4.0
        if source_label and (source_label in query or query in source_label):
            score += 4.0

        haystack_tokens = set(haystack.split())
        overlap = query_tokens & haystack_tokens
        if query_tokens:
            score += len(overlap) / len(query_tokens) * 3.0

        if score > best_score:
            best_score = score
            best_item = item

    return best_item if best_score >= 2.0 else None


def parser_menu_candidates(production_menu: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    candidates = []
    for item in production_menu or []:
        candidates.append({
            "id": item.get("id"),
            "title": item.get("title"),
            "source_label": item.get("source_label"),
            "type": item.get("type"),
            "member_type": item.get("primary_member_type"),
            "objective": item.get("objective"),
            "platform": item.get("primary_platform"),
            "quantity": item.get("suggested_quantity"),
            "angle": item.get("angle"),
            "production_notes": item.get("production_notes"),
        })
    return candidates


def parse_setup_instruction(
    instruction: str,
    current_setup: dict[str, Any] | None = None,
    conversation_history: list[dict[str, Any]] | None = None,
    production_menu: list[dict[str, Any]] | None = None,
    usage_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not instruction or not instruction.strip():
        raise HTTPException(status_code=400, detail="Instruction is required.")

    settings = get_settings()
    parser_prompt = """
You are Escouade's setup parser.

Your job is to convert a user's natural-language production setup instruction into structured setup updates.
You do not generate posts. You do not revise posts. You only return fields that should change.

Parse concrete setup details such as:
- content format/member type: carrousel, reel, image_post, stories, text_post
- saved Sacha production menu/brief selection
- batch name, source type, source label, selected series
- quantity
- platform / output target
- objective, CTA preference, language, content style, interaction style
- production table columns
- fixed fields that repeat on every row
- variable fields that should change row by row
- media placeholder columns
- format filters
- extra special instructions

Rules:
- If the user says "no CTA", set cta_preference to "No CTA".
- If the user says "image posts", set member_type to "image_post".
- If the user says "carousel", set member_type to "carrousel".
- If the user asks to use a saved Sacha series/brief/menu item, choose the best matching sacha_production_menu_candidates id and set matched_brief_id.
- If the user says columns like "Title/Image/Headline/Subheadline/Caption", return production_columns in that exact order.
- Column labels should be human-readable. Use type "media_placeholder" for image/video/media columns.
- Do not invent missing details. Leave unknown fields null/empty.
- If the instruction is only about revision, approval, export, or feedback on existing posts, return no updates.
- Keep B10X wording. Do not mention GHL, GoHighLevel, HighLevel, LeadConnector, or backend provider names.
"""
    payload = {
        "current_setup": current_setup or {},
        "conversation_history": (conversation_history or [])[-20:],
        "instruction": instruction,
        "sacha_production_menu_candidates": parser_menu_candidates(production_menu),
        "allowed_member_types": ["carrousel", "reel", "image_post", "stories", "text_post"],
        "example": {
            "instruction": "Make 10 image posts, no CTA, columns Title/Image/Headline/Subheadline/Caption",
            "updates": {
                "member_type": "image_post",
                "quantity": 10,
                "cta_preference": "No CTA",
                "production_columns": [
                    {"label": "Title", "type": "text"},
                    {"label": "Image", "type": "media_placeholder"},
                    {"label": "Headline", "type": "text"},
                    {"label": "Subheadline", "type": "text"},
                    {"label": "Caption", "type": "long_text"},
                ],
                "media_placeholders": ["Image"],
            },
        },
    }
    llm = build_llm().with_structured_output(SetupParserOutput, include_raw=True, method="function_calling")
    result = llm.invoke([
        SystemMessage(content="\n\n".join([parser_prompt, read_platform_knowledge()])),
        HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str)),
    ])

    raw_response = result.get("raw") if isinstance(result, dict) else None
    if raw_response is not None:
        record_token_usage(
            session=usage_context,
            agent_id="escouade",
            model=settings.escouade_model,
            response=raw_response,
            metadata={"operation": "setup_parse"},
        )

    if isinstance(result, dict):
        if result.get("parsing_error"):
            raise HTTPException(status_code=502, detail=f"Unable to parse setup instruction: {result['parsing_error']}")
        parsed = result.get("parsed")
    else:
        parsed = result

    if parsed is None:
        raise HTTPException(status_code=502, detail="Unable to parse setup instruction.")

    deterministic_match = match_sacha_menu_item(instruction, production_menu)
    matched_brief_id = parsed.matched_brief_id or (str(deterministic_match.get("id")) if deterministic_match else None)
    matched_item = None
    if matched_brief_id:
        matched_item = next((item for item in production_menu or [] if str(item.get("id")) == str(matched_brief_id)), None)
    if not matched_item:
        matched_item = deterministic_match
        matched_brief_id = str(matched_item.get("id")) if matched_item else None

    updates = normalize_setup_parser_updates(parsed)
    updated_fields = [field for field in parsed.updated_fields if field in updates] or list(updates.keys())
    message = parsed.message or "Setup updated."
    if matched_item:
        message = f"Applied Sacha brief: {matched_item.get('title') or matched_item.get('source_label') or matched_brief_id}."
    elif not updates:
        message = "No setup changes detected."

    return {
        "updates": updates,
        "updated_fields": updated_fields,
        "matched_brief_id": matched_brief_id,
        "matched_brief_title": matched_item.get("title") if matched_item else None,
        "matched_brief": matched_item.get("brief") if matched_item else None,
        "message": message,
    }


def build_generation_messages(
    member_type: str,
    production_brief: dict[str, Any],
    strategy_review: dict[str, Any],
    filters: dict[str, Any],
    instruction: str,
    conversation_history: list[dict[str, Any]],
    current_items: list[dict[str, Any]] | None = None,
) -> list:
    master_prompt = read_prompt("master")
    member_prompt = read_prompt(member_type)
    default_columns = default_production_columns(member_type, filters)
    requested_columns = setup_production_columns(filters)
    payload = {
        "production_brief": production_brief,
        "strategy_review": strategy_review,
        "filters": filters,
        "default_production_columns": requested_columns or default_columns,
        "member_quality_profile": member_quality_profile(member_type, filters),
        "structured_setup": {
            "output_target": filters.get("output_target"),
            "production_columns": requested_columns,
            "fixed_fields": filters.get("fixed_fields") or {},
            "variable_fields": filters.get("variable_fields") or [],
            "media_placeholders": filters.get("media_placeholders") or [],
        },
        "output_target_rules": output_target_rules(filters),
        "production_table_rules": {
            "purpose": "Return CSV-ready rows for the production table. The user should not need to copy/paste from chat.",
            "column_policy": "If structured_setup.production_columns is present, use those columns exactly, in that order, with no extra columns. Otherwise use default_production_columns unless the brief clearly requires custom columns.",
            "row_policy": "Every item.table_row must include values for the chosen production_columns keys only. Media placeholder columns must be empty strings unless a real URL exists.",
            "fixed_field_policy": "Values in structured_setup.fixed_fields must repeat exactly in every matching table_row column.",
            "variable_field_policy": "Fields listed in structured_setup.variable_fields must change row-by-row and should not repeat the same value across the whole batch.",
            "revision_policy": "During revision, preserve existing table columns unless the user explicitly asks to add/remove/change columns.",
        },
        "current_items": current_items or [],
        "conversation_history": conversation_history[-20:],
        "user_instruction": instruction,
    }
    return [
        SystemMessage(content="\n\n".join([master_prompt, member_prompt, read_platform_knowledge()])),
        HumanMessage(content=json.dumps(payload, ensure_ascii=False, default=str)),
    ]


def call_structured_generation(
    member_type: str,
    production_brief: dict[str, Any],
    strategy_review: dict[str, Any],
    filters: dict[str, Any],
    instruction: str,
    conversation_history: list[dict[str, Any]],
    current_items: list[dict[str, Any]] | None = None,
    usage_context: dict[str, Any] | None = None,
    usage_metadata: dict[str, Any] | None = None,
):
    schema = MEMBER_OUTPUT_SCHEMAS[member_type]
    settings = get_settings()
    llm = build_llm().with_structured_output(schema, include_raw=True, method="function_calling")
    messages = build_generation_messages(member_type, production_brief, strategy_review, filters, instruction, conversation_history, current_items)

    last_error = None
    for attempt in range(2):
        try:
            result = llm.invoke(messages)
            raw_response = result.get("raw") if isinstance(result, dict) else None
            if raw_response is not None:
                record_token_usage(
                    session=usage_context,
                    agent_id="escouade",
                    model=settings.escouade_model,
                    response=raw_response,
                    metadata={
                        "member_type": member_type,
                        "attempt": attempt + 1,
                        **(usage_metadata or {}),
                    },
                )

            if isinstance(result, dict):
                if result.get("parsing_error"):
                    raise result["parsing_error"]
                parsed = result.get("parsed")
                if parsed is None:
                    raise ValueError("Structured output parser returned no parsed payload.")
                return parsed

            return result
        except Exception as exc:
            last_error = exc
            messages.append(
                HumanMessage(
                    content=(
                        "The previous structured output attempt failed validation. "
                        f"Return a valid response matching the schema. Error: {exc}"
                    )
                )
            )

    raise HTTPException(status_code=502, detail=f"Escouade generation failed schema validation: {last_error}")


def create_or_update_production_table(
    db: Session,
    batch: EscouadeBatch,
    columns: list[dict[str, Any]],
    export_format: str | None = None,
    increment_version: bool = False,
) -> EscouadeProductionTable:
    export_format = export_format or output_export_format(batch.filters or {})
    if batch.production_table:
        batch.production_table.columns = columns
        batch.production_table.export_format = export_format
        if increment_version:
            batch.production_table.version += 1
        db.flush()
        return batch.production_table

    table = EscouadeProductionTable(
        batch_id=batch.id,
        location_id=batch.location_id,
        columns=columns,
        export_format=export_format,
    )
    db.add(table)
    db.flush()
    return table


def upsert_production_row(
    db: Session,
    batch: EscouadeBatch,
    item: EscouadeItem,
    row_data: dict[str, Any],
    increment_version: bool = False,
) -> EscouadeProductionRow:
    existing = item.production_row
    if not existing:
        existing = db.scalars(
            select(EscouadeProductionRow).where(
                EscouadeProductionRow.batch_id == batch.id,
                EscouadeProductionRow.item_id == item.id,
            )
        ).first()

    if existing:
        existing.post_id = item.post_id
        existing.row_data = row_data
        if increment_version:
            existing.version += 1
        db.flush()
        return existing

    row = EscouadeProductionRow(
        batch_id=batch.id,
        item_id=item.id,
        location_id=batch.location_id,
        post_id=item.post_id,
        row_data=row_data,
    )
    db.add(row)
    db.flush()
    return row


def generate_batch(
    db: Session,
    location_id: str,
    usage_context: dict[str, Any] | None,
    member_type: str,
    batch_name: str | None,
    source_type: str | None,
    source_label: str | None,
    filters: EscouadeBatchFilters,
    instruction: str,
    conversation_history: list[dict[str, Any]],
) -> EscouadeBatch:
    if member_type not in MEMBER_OUTPUT_SCHEMAS:
        raise HTTPException(status_code=400, detail="Unsupported Escouade member type.")

    knowledge_context = load_knowledge_context(db, location_id)
    missing_context = [agent_id for agent_id, value in knowledge_context.items() if not value]
    if missing_context:
        raise HTTPException(
            status_code=409,
            detail=f"Escouade requires completed upstream context first: {', '.join(missing_context)}.",
        )

    batch = EscouadeBatch(
        location_id=location_id,
        member_type=member_type,
        batch_name=batch_name or source_label or f"{member_type.replace('_', ' ').title()} Batch",
        source_type=source_type or filters.source_type,
        source_label=source_label or filters.source_label,
        filters=filters.model_dump(),
        status="draft",
    )
    db.add(batch)
    db.flush()

    filters_data = filters.model_dump()
    production_brief = build_production_brief(knowledge_context, member_type, filters_data)
    strategy_review = build_strategy_review(member_type, filters_data, production_brief)
    batch.strategy_review = strategy_review

    generated = call_structured_generation(
        member_type,
        production_brief,
        strategy_review,
        filters_data,
        instruction,
        conversation_history,
        usage_context=usage_context,
        usage_metadata={"operation": "generate"},
    )
    item_schema = MEMBER_ITEM_SCHEMAS[member_type]
    requested_columns = setup_production_columns(filters_data)
    columns = requested_columns or normalize_production_columns(getattr(generated, "production_columns", None), member_type, filters_data)
    validated_contents, row_data_list = validated_contents_and_rows(member_type, generated, item_schema, columns, filters_data)
    variable_violations = variable_rule_violations(row_data_list, filters_data)
    if variable_violations:
        generated = call_structured_generation(
            member_type,
            production_brief,
            strategy_review,
            filters_data,
            (
                f"{instruction}\n\nCorrection required: the following variable fields must change row by row "
                f"and must not repeat the same value across all items: {', '.join(variable_violations)}. "
                "Keep the exact requested production columns, keep fixed fields repeated exactly, and leave media placeholder columns empty."
            ),
            conversation_history,
            usage_context=usage_context,
            usage_metadata={"operation": "generate_retry_table_rules", "violations": variable_violations},
        )
        columns = requested_columns or normalize_production_columns(getattr(generated, "production_columns", None), member_type, filters_data)
        validated_contents, row_data_list = validated_contents_and_rows(member_type, generated, item_schema, columns, filters_data)
    create_or_update_production_table(db, batch, columns, export_format=output_export_format(filters_data))

    for index, content in enumerate(validated_contents, start=1):
        post_id = content.get("post_id") or f"{member_type.upper()}-{index:03d}"
        row_data = row_data_list[index - 1] if index - 1 < len(row_data_list) else row_from_content(member_type, content, columns, filters_data)
        content["table_row"] = row_data
        item = EscouadeItem(
            batch_id=batch.id,
            location_id=location_id,
            post_id=post_id,
            member_type=member_type,
            content=content,
            status="draft",
        )
        db.add(item)
        db.flush()
        upsert_production_row(db, batch, item, row_data)

    batch.quality_note = generated.quality_note
    db.flush()
    return get_batch_or_404(db, batch.id, location_id)


def filter_editable_items(db: Session, location_id: str, batch_id: UUID | str, item_ids: list[UUID]) -> tuple[list[EscouadeItem], list[EscouadeItem]]:
    items = db.scalars(
        select(EscouadeItem).where(
            EscouadeItem.batch_id == batch_id,
            EscouadeItem.location_id == location_id,
            EscouadeItem.id.in_(item_ids),
        )
    ).all()
    found_ids = {item.id for item in items}
    missing_ids = [str(item_id) for item_id in item_ids if item_id not in found_ids]
    if missing_ids:
        raise HTTPException(status_code=404, detail=f"Items not found: {', '.join(missing_ids)}")

    editable = [item for item in items if item.status in EDITABLE_STATUSES]
    locked = [item for item in items if item.status in LOCKED_STATUSES]
    return editable, locked


def revise_items(
    db: Session,
    location_id: str,
    usage_context: dict[str, Any] | None,
    batch_id: UUID,
    item_ids: list[UUID],
    instruction: str,
    conversation_history: list[dict[str, Any]],
) -> tuple[EscouadeBatch, list[EscouadeItem]]:
    batch = get_batch_or_404(db, batch_id, location_id)
    editable, locked = filter_editable_items(db, location_id, batch_id, item_ids)

    if locked:
        locked_labels = ", ".join(f"{item.post_id} ({item.status})" for item in locked)
        raise HTTPException(status_code=409, detail=f"Locked items cannot be revised. Reopen them first: {locked_labels}")
    if not editable:
        raise HTTPException(status_code=409, detail="No editable items were selected.")

    knowledge_context = load_knowledge_context(db, location_id)
    production_brief = build_production_brief(knowledge_context, batch.member_type, batch.filters or {})
    strategy_review = batch.strategy_review or build_strategy_review(batch.member_type, batch.filters or {}, production_brief)
    current_items = [{"id": str(item.id), "post_id": item.post_id, **item.content} for item in editable]
    generated = call_structured_generation(
        batch.member_type,
        production_brief,
        strategy_review,
        batch.filters,
        instruction,
        conversation_history,
        current_items=current_items,
        usage_context=usage_context,
        usage_metadata={"operation": "revise", "batch_id": str(batch_id)},
    )

    item_schema = MEMBER_ITEM_SCHEMAS[batch.member_type]
    should_update_columns = bool(re.search(r"\b(column|columns|field|fields|table|csv)\b", instruction or "", re.IGNORECASE))
    requested_columns = setup_production_columns(batch.filters or {})
    columns = requested_columns or (batch.production_table.columns if batch.production_table else None) or default_production_columns(batch.member_type, batch.filters or {})
    generated_columns = getattr(generated, "production_columns", None)
    if requested_columns:
        create_or_update_production_table(db, batch, columns, export_format=output_export_format(batch.filters or {}), increment_version=False)
    elif should_update_columns and generated_columns:
        columns = normalize_production_columns(generated_columns, batch.member_type, batch.filters or {})
        create_or_update_production_table(db, batch, columns, export_format=output_export_format(batch.filters or {}), increment_version=True)
    elif not batch.production_table:
        create_or_update_production_table(db, batch, columns, export_format=output_export_format(batch.filters or {}))

    validated_contents, row_data_list = validated_contents_and_rows(batch.member_type, generated, item_schema, columns, batch.filters or {})
    variable_violations = variable_rule_violations(row_data_list, batch.filters or {})
    if variable_violations:
        generated = call_structured_generation(
            batch.member_type,
            production_brief,
            strategy_review,
            batch.filters,
            (
                f"{instruction}\n\nCorrection required: the following variable fields must change row by row "
                f"and must not repeat the same value across all revised items: {', '.join(variable_violations)}. "
                "Preserve exact table columns, keep fixed fields repeated exactly, and leave media placeholder columns empty."
            ),
            conversation_history,
            current_items=current_items,
            usage_context=usage_context,
            usage_metadata={"operation": "revise_retry_table_rules", "batch_id": str(batch_id), "violations": variable_violations},
        )
        validated_contents, row_data_list = validated_contents_and_rows(batch.member_type, generated, item_schema, columns, batch.filters or {})

    generated_by_post_id = {}
    row_data_by_post_id = {}
    for index, validated in enumerate(validated_contents):
        generated_by_post_id[validated["post_id"]] = validated
        if index < len(row_data_list):
            row_data_by_post_id[validated["post_id"]] = row_data_list[index]

    for item in editable:
        updated_content = generated_by_post_id.get(item.post_id)
        if not updated_content:
            continue
        row_data = row_data_by_post_id.get(item.post_id) or row_from_content(batch.member_type, updated_content, columns, batch.filters or {})
        updated_content["table_row"] = row_data
        item.content = updated_content
        item.status = "revised"
        item.version += 1
        upsert_production_row(db, batch, item, row_data, increment_version=True)

    batch.quality_note = generated.quality_note or batch.quality_note
    db.flush()
    return get_batch_or_404(db, batch_id, location_id), locked


def approve_items(db: Session, location_id: str, batch_id: UUID, item_ids: list[UUID]) -> EscouadeBatch:
    batch = get_batch_or_404(db, batch_id, location_id)
    items = db.scalars(
        select(EscouadeItem).where(
            EscouadeItem.batch_id == batch.id,
            EscouadeItem.location_id == location_id,
            EscouadeItem.id.in_(item_ids),
        )
    ).all()
    for item in items:
        if item.status in EDITABLE_STATUSES:
            item.status = "approved"
    db.flush()
    return get_batch_or_404(db, batch.id, location_id)


def reopen_items(db: Session, location_id: str, batch_id: UUID, item_ids: list[UUID]) -> EscouadeBatch:
    batch = get_batch_or_404(db, batch_id, location_id)
    items = db.scalars(
        select(EscouadeItem).where(
            EscouadeItem.batch_id == batch.id,
            EscouadeItem.location_id == location_id,
            EscouadeItem.id.in_(item_ids),
        )
    ).all()
    for item in items:
        if item.status == "approved":
            item.status = "needs_revision"
        elif item.status == "exported":
            raise HTTPException(status_code=409, detail=f"{item.post_id} has already been exported and cannot be reopened.")
    db.flush()
    return get_batch_or_404(db, batch.id, location_id)


def export_approved_csv(db: Session, location_id: str, batch_id: UUID) -> tuple[EscouadeBatch, str, str]:
    batch = get_batch_or_404(db, batch_id, location_id)
    approved_items = [item for item in batch.items if item.status == "approved"]
    if not approved_items:
        raise HTTPException(status_code=409, detail="No approved items are available for export.")

    rows_by_item_id = {row.item_id: row for row in batch.production_rows}
    approved_rows = [
        rows_by_item_id[item.id].row_data
        for item in approved_items
        if item.id in rows_by_item_id
    ]
    if batch.production_table and approved_rows:
        content = build_production_table_csv(batch.production_table.columns or [], approved_rows)
    else:
        content = build_items_csv(batch.member_type, [item.content for item in approved_items])
    for item in approved_items:
        item.status = "exported"
    batch.status = "exported"
    db.flush()
    export_format = batch.production_table.export_format if batch.production_table else output_export_format(batch.filters or {})
    export_slug = export_format_slug(export_format)
    member_slug = re.sub(r"[^a-zA-Z0-9]+", "-", batch.member_type.replace("_", "-")).strip("-").lower()
    label = re.sub(r"[^a-zA-Z0-9]+", "-", batch.batch_name or batch.source_label or str(batch.id)).strip("-").lower()
    filename = f"escouade-{export_slug}-{member_slug}-{label or batch.id}.csv"
    return get_batch_or_404(db, batch.id, location_id), content, filename


def get_items_by_post_ids(batch: EscouadeBatch, post_ids: list[str]) -> list[EscouadeItem]:
    normalized = {post_id.upper() for post_id in post_ids}
    return [item for item in batch.items if item.post_id.upper() in normalized]


def expand_post_id_range(message: str, batch: EscouadeBatch) -> list[str]:
    all_post_ids = [item.post_id for item in batch.items]
    range_match = re.search(r"([A-Z]+-\d{3})\s*(?:to|-|through)\s*([A-Z]+-\d{3})", message, re.IGNORECASE)
    if range_match:
        start, end = range_match.group(1).upper(), range_match.group(2).upper()
        prefix = start.split("-")[0]
        start_num = int(start.split("-")[1])
        end_num = int(end.split("-")[1])
        return [f"{prefix}-{num:03d}" for num in range(min(start_num, end_num), max(start_num, end_num) + 1)]

    return [match.upper() for match in re.findall(r"\b[A-Z]+-\d{3}\b", message, flags=re.IGNORECASE) if match.upper() in {item.upper() for item in all_post_ids}]


def editable_item_ids_for_command(batch: EscouadeBatch, message: str) -> list[UUID]:
    lower = message.lower()
    if "all" in lower and ("draft" in lower or "non-approved" in lower or "non approved" in lower or "editable" in lower):
        return [item.id for item in batch.items if item.status in EDITABLE_STATUSES]

    post_ids = expand_post_id_range(message, batch)
    if post_ids:
        return [item.id for item in get_items_by_post_ids(batch, post_ids)]

    return []


def handle_command(
    db: Session,
    location_id: str,
    batch_id: UUID,
    message: str,
    conversation_history: list[dict[str, Any]],
    usage_context: dict[str, Any] | None = None,
) -> tuple[EscouadeBatch, str, str | None]:
    batch = get_batch_or_404(db, batch_id, location_id)
    lower = message.lower()
    item_ids = editable_item_ids_for_command(batch, message)

    if "export" in lower:
        batch, _csv_content, filename = export_approved_csv(db, location_id, batch_id)
        return batch, f"Approved items exported as {filename}.", filename

    if "approve" in lower:
        if not item_ids:
            item_ids = [item.id for item in batch.items if item.status in EDITABLE_STATUSES]
        batch = approve_items(db, location_id, batch_id, item_ids)
        return batch, "Selected items approved and locked.", None

    if "reopen" in lower:
        post_ids = expand_post_id_range(message, batch)
        selected = [item.id for item in get_items_by_post_ids(batch, post_ids)] if post_ids else []
        if not selected:
            raise HTTPException(status_code=400, detail="Tell Escouade which approved item IDs to reopen.")
        batch = reopen_items(db, location_id, batch_id, selected)
        return batch, "Selected approved items reopened for revision.", None

    revise_terms = ("revise", "regenerate", "rewrite", "stronger", "more", "less", "make")
    if any(term in lower for term in revise_terms):
        if not item_ids:
            raise HTTPException(status_code=400, detail="Tell Escouade which editable item IDs to revise, or say all editable items.")
        batch, _locked = revise_items(db, location_id, usage_context, batch_id, item_ids, message, conversation_history)
        return batch, batch.quality_note or "Editable items revised and marked as revised.", None

    raise HTTPException(
        status_code=400,
        detail="I can approve, reopen, revise, regenerate, or export. Include item IDs like IMG-001 or say all editable items.",
    )
