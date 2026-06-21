import json
import re
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import HTTPException
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload

from app.agents.common import read_platform_knowledge
from app.core.config import get_settings
from app.escouade.csv import build_items_csv
from app.escouade.models import EscouadeBatch, EscouadeItem
from app.escouade.schemas.common import EscouadeBatchFilters
from app.escouade.schemas.member_outputs import MEMBER_ITEM_SCHEMAS, MEMBER_OUTPUT_SCHEMAS
from app.services.escouade_brief import extract_escouade_brief
from app.services.token_usage import record_token_usage

PROMPT_DIR = Path(__file__).with_name("prompts")
EDITABLE_STATUSES = {"draft", "needs_revision", "revised"}
LOCKED_STATUSES = {"approved", "exported"}


def read_prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")


def require_location_id(session_context: dict) -> str:
    location_id = session_context.get("activeLocation") or session_context.get("locationId")
    if not location_id:
        raise HTTPException(status_code=400, detail="Unable to determine location context.")
    return location_id


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


def get_batch_or_404(db: Session, batch_id: UUID | str, location_id: str) -> EscouadeBatch:
    batch = db.scalars(
        select(EscouadeBatch)
        .options(selectinload(EscouadeBatch.items))
        .where(EscouadeBatch.id == batch_id, EscouadeBatch.location_id == location_id)
    ).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Escouade batch not found.")
    return batch


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
    if not brief:
        brief = extract_escouade_brief(row.get("final_output") or "")

    return {
        "brief": brief or None,
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
    payload = {
        "production_brief": production_brief,
        "strategy_review": strategy_review,
        "filters": filters,
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
    llm = build_llm().with_structured_output(schema, include_raw=True)
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

    for index, generated_item in enumerate(generated.items, start=1):
        validated = item_schema.model_validate(generated_item)
        content = validated.model_dump()
        post_id = content.get("post_id") or f"{member_type.upper()}-{index:03d}"
        db.add(
            EscouadeItem(
                batch_id=batch.id,
                location_id=location_id,
                post_id=post_id,
                member_type=member_type,
                content=content,
                status="draft",
            )
        )

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
    generated_by_post_id = {
        item.post_id: item_schema.model_validate(item).model_dump()
        for item in generated.items
    }

    for item in editable:
        updated_content = generated_by_post_id.get(item.post_id)
        if not updated_content:
            continue
        item.content = updated_content
        item.status = "revised"
        item.version += 1

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

    content = build_items_csv(batch.member_type, [item.content for item in approved_items])
    for item in approved_items:
        item.status = "exported"
    batch.status = "exported"
    db.flush()
    label = re.sub(r"[^a-zA-Z0-9]+", "-", batch.batch_name or batch.source_label or str(batch.id)).strip("-").lower()
    filename = f"escouade-{batch.member_type}-{label or batch.id}.csv"
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
