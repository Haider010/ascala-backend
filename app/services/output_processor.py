import json
import re
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.db.schema import ensure_agent_outputs_tables
from app.db.session import db_connection

logger = get_logger()

OUTPUT_TABLES = {
    "molly": "molly_outputs",
    "brandy": "brandy_outputs",
    "sacha": "sacha_outputs",
}

OUTPUT_PATTERN = re.compile(
    r"<!--\s*ASCALA_OUTPUT_START\s+(?P<attrs>.*?)-->\s*(?P<body>.*?)\s*<!--\s*ASCALA_OUTPUT_END\s*-->",
    re.IGNORECASE | re.DOTALL,
)
PATCH_PATTERN = re.compile(
    r"<!--\s*ASCALA_PATCH_START\s+(?P<attrs>.*?)-->\s*(?P<body>.*?)\s*<!--\s*ASCALA_PATCH_END\s*-->",
    re.IGNORECASE | re.DOTALL,
)
MARKER_LINE_PATTERN = re.compile(
    r"<!--\s*ASCALA_(?:OUTPUT|PATCH)_(?:START|END)\b.*?-->\s*",
    re.IGNORECASE | re.DOTALL,
)
ATTR_PATTERN = re.compile(r'([a-zA-Z_][\w-]*)="([^"]*)"')
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def extract_response_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload

    if isinstance(payload, list):
        parts = [extract_response_text(item) for item in payload]
        return "\n".join(part for part in parts if part)

    if isinstance(payload, dict):
        for key in ("output", "response", "text", "message", "content", "answer"):
            value = payload.get(key)
            if isinstance(value, str):
                return value

        for key in ("data", "payload", "result"):
            nested = extract_response_text(payload.get(key))
            if nested:
                return nested

    return ""


def parse_attrs(raw_attrs: str) -> dict:
    return {key: value for key, value in ATTR_PATTERN.findall(raw_attrs or "")}


def clean_marker_text(text: str) -> str:
    text = OUTPUT_PATTERN.sub(lambda match: match.group("body").strip(), text)
    text = PATCH_PATTERN.sub(lambda match: match.group("body").strip(), text)
    text = MARKER_LINE_PATTERN.sub("", text)
    return text.strip()


def strip_ascala_markers(text: str) -> str:
    return clean_marker_text(text)


def strip_markers_from_payload(payload: Any) -> Any:
    if isinstance(payload, str):
        return strip_ascala_markers(payload)

    if isinstance(payload, list):
        return [strip_markers_from_payload(item) for item in payload]

    if isinstance(payload, dict):
        return {
            key: strip_markers_from_payload(value)
            for key, value in payload.items()
        }

    return payload


def normalize_key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower())
    return normalized.strip("_")


def target_label(target_path: str) -> str:
    labels = {
        "positioning": "POSITIONING",
        "unique_angle": "Unique angle",
        "transformation": "TRANSFORMATION",
        "emotional_shift": "Emotional shift",
        "offer_ecosystem": "OFFER ECOSYSTEM",
        "entry_points": "Entry points",
        "priority_map": "PRIORITY MAP",
        "top_content_themes": "Top content themes",
        "ideal_client_avatar": "IDEAL CLIENT AVATAR",
        "core_attributes": "CORE ATTRIBUTES",
        "deep_psychology": "DEEP PSYCHOLOGY",
        "buyer_behavior": "BUYER BEHAVIOR",
        "language_and_attention": "LANGUAGE & ATTENTION",
        "humor_triggers": "Humor triggers",
        "content_habits": "CONTENT HABITS",
        "market": "MARKET",
        "voice_system": "VOICE SYSTEM",
        "words_to_use": "Words to use",
        "words_to_avoid": "Words to avoid",
        "content_intent": "CONTENT INTENT",
        "content_pillars": "CONTENT PILLARS",
        "angles_library": "ANGLES LIBRARY",
        "offer_alignment_map": "OFFER ALIGNMENT MAP",
        "handoff_structure": "HANDOFF STRUCTURE",
        "content_engine": "CONTENT ENGINE",
        "hook_library": "Hook library",
        "objection_content_bank": "Objection content bank",
        "platform_strategy": "Platform strategy",
        "performance_loop": "Performance loop",
        "social_media_audit": "Social Media Audit & Continuity Analysis",
        "strategic_content_themes": "Strategic Content Themes",
        "recurring_content_series": "Recurring Content Series",
        "one_off_content_opportunities": "One-Off Content Opportunities",
        "monthly_strategic_plan": "Monthly Strategic Plan",
        "weekly_content_plan": "Weekly Content Plan",
        "social_media_calendar": "Social Media Calendar",
        "campaign_launch_plans": "Campaign & Launch Plans",
        "platform_specific_strategy": "Platform-Specific Strategy",
        "posting_cadence_recommendation": "Posting Cadence Recommendation",
        "cta_direction_map": "CTA Direction Map",
        "repurposing_opportunities": "Repurposing Opportunities",
        "production_readiness_assessment": "Production Readiness Assessment",
    }
    key = target_path.split(".")[-1]
    return labels.get(key, key.replace("_", " ").title())


def normalize_heading_title(heading: str) -> str:
    heading = re.sub(r"^section\s+\d+\s*:\s*", "", heading.strip(), flags=re.IGNORECASE)
    return re.sub(r"^\d+(?:\.\d+)*[\s.):-]+", "", heading).strip()


def set_nested_value(data: dict, target_path: str, value: Any, mode: str = "replace") -> dict:
    if not target_path:
        return data

    parts = [normalize_key(part) for part in target_path.split(".") if part.strip()]
    current = data
    for part in parts[:-1]:
        current = current.setdefault(part, {})

    final_key = parts[-1]
    if mode == "append":
        existing = current.get(final_key)
        if isinstance(existing, list):
            existing.append(value)
            current[final_key] = existing
        elif existing:
            current[final_key] = [existing, value]
        else:
            current[final_key] = [value]
    elif mode == "remove":
        current.pop(final_key, None)
    else:
        current[final_key] = value

    return data


def parse_markdown_sections(markdown: str) -> dict:
    sections = {}
    matches = list(HEADING_PATTERN.finditer(markdown))

    for index, match in enumerate(matches):
        heading = match.group(2).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        sections[normalize_key(heading)] = {
            "heading": heading,
            "level": len(match.group(1)),
            "content": markdown[start:end].strip(),
        }

    return sections


def build_structured_output(markdown: str, output_type: str | None = None) -> dict:
    return {
        "output_type": output_type,
        "document_markdown": markdown,
        "sections": parse_markdown_sections(markdown),
        "patches": [],
    }


def find_markdown_section_bounds(markdown: str, label: str) -> tuple[int, int] | None:
    normalized_label = normalize_key(label)
    matches = list(HEADING_PATTERN.finditer(markdown))

    for index, match in enumerate(matches):
        heading = match.group(2).strip()
        heading_key = normalize_key(normalize_heading_title(heading))
        if normalized_label not in {normalize_key(heading), heading_key}:
            continue

        start = match.start()
        current_level = len(match.group(1))
        end = len(markdown)

        for next_match in matches[index + 1 :]:
            if len(next_match.group(1)) <= current_level:
                end = next_match.start()
                break

        return start, end

    return None


def apply_markdown_patch(markdown: str, target_path: str, patch_markdown: str, mode: str) -> str:
    label = target_label(target_path)
    bounds = find_markdown_section_bounds(markdown, label)

    if mode == "append":
        addition = f"\n\n{patch_markdown.strip()}"
        if bounds:
            start, end = bounds
            return f"{markdown[:end].rstrip()}{addition}\n\n{markdown[end:].lstrip()}".strip()
        return f"{markdown.rstrip()}\n\n{patch_markdown.strip()}".strip()

    if mode == "remove":
        if bounds:
            start, end = bounds
            return f"{markdown[:start].rstrip()}\n\n{markdown[end:].lstrip()}".strip()
        return markdown

    if bounds:
        start, end = bounds
        return f"{markdown[:start].rstrip()}\n\n{patch_markdown.strip()}\n\n{markdown[end:].lstrip()}".strip()

    return f"{markdown.rstrip()}\n\n## Updated: {label}\n\n{patch_markdown.strip()}".strip()


def upsert_full_output(
    agent_id: str,
    location_id: str,
    output_type: str | None,
    markdown: str,
    session_id: str | None,
) -> None:
    table_name = OUTPUT_TABLES[agent_id]
    now = datetime.now(UTC).isoformat()
    structured_output = build_structured_output(markdown, output_type)
    metadata = {
        "classification": "full_final_output",
        "confidence": 1.0,
        "detected_by": "ascala_marker",
        "processed_at": now,
    }

    with db_connection() as conn:
        cursor = conn.cursor()
        try:
            ensure_agent_outputs_tables(cursor)
            cursor.execute(
                f"""
                INSERT INTO {table_name} (
                    location_id, final_output, structured_output, output_type,
                    metadata, source_session_id, updated_at
                )
                VALUES (%s, %s, %s::jsonb, %s, %s::jsonb, %s, NOW())
                ON CONFLICT (location_id)
                DO UPDATE SET
                    final_output = EXCLUDED.final_output,
                    structured_output = EXCLUDED.structured_output,
                    output_type = EXCLUDED.output_type,
                    metadata = {table_name}.metadata || EXCLUDED.metadata,
                    source_session_id = EXCLUDED.source_session_id,
                    version = {table_name}.version + 1,
                    updated_at = NOW()
                """,
                (
                    location_id,
                    markdown,
                    json.dumps(structured_output),
                    output_type,
                    json.dumps(metadata),
                    session_id,
                ),
            )
            conn.commit()
        finally:
            cursor.close()


def apply_output_patch(
    agent_id: str,
    location_id: str,
    output_type: str | None,
    target: str,
    mode: str,
    patch_markdown: str,
    session_id: str | None,
) -> None:
    table_name = OUTPUT_TABLES[agent_id]
    now = datetime.now(UTC).isoformat()

    with db_connection() as conn:
        cursor = conn.cursor()
        try:
            ensure_agent_outputs_tables(cursor)
            cursor.execute(
                f"""
                SELECT final_output, structured_output, metadata
                FROM {table_name}
                WHERE location_id = %s
                """,
                (location_id,),
            )
            row = cursor.fetchone()

            existing_markdown = row[0] if row else ""
            structured_output = row[1] if row and isinstance(row[1], dict) else {}
            metadata = row[2] if row and isinstance(row[2], dict) else {}

            if not structured_output:
                structured_output = build_structured_output(existing_markdown, output_type)

            updated_markdown = apply_markdown_patch(existing_markdown, target, patch_markdown, mode)
            structured_output["document_markdown"] = updated_markdown
            structured_output["sections"] = parse_markdown_sections(updated_markdown)
            set_nested_value(structured_output, target, patch_markdown, mode)
            structured_output.setdefault("patches", []).append({
                "target": target,
                "mode": mode,
                "content": patch_markdown,
                "processed_at": now,
            })

            patch_metadata = {
                **metadata,
                "classification": "partial_update",
                "confidence": 1.0,
                "detected_by": "ascala_marker",
                "last_patch": {
                    "target": target,
                    "mode": mode,
                    "processed_at": now,
                },
            }

            cursor.execute(
                f"""
                INSERT INTO {table_name} (
                    location_id, final_output, structured_output, output_type,
                    metadata, source_session_id, updated_at
                )
                VALUES (%s, %s, %s::jsonb, %s, %s::jsonb, %s, NOW())
                ON CONFLICT (location_id)
                DO UPDATE SET
                    final_output = EXCLUDED.final_output,
                    structured_output = EXCLUDED.structured_output,
                    output_type = COALESCE(EXCLUDED.output_type, {table_name}.output_type),
                    metadata = EXCLUDED.metadata,
                    source_session_id = EXCLUDED.source_session_id,
                    version = {table_name}.version + 1,
                    updated_at = NOW()
                """,
                (
                    location_id,
                    updated_markdown,
                    json.dumps(structured_output),
                    output_type,
                    json.dumps(patch_metadata),
                    session_id,
                ),
            )
            conn.commit()
        finally:
            cursor.close()


def process_agent_output(session: dict, agent_id: str, payload: Any, session_id: str | None = None) -> None:
    if agent_id not in OUTPUT_TABLES:
        return

    location_id = session.get("activeLocation") or session.get("locationId")
    if not location_id:
        logger.info("[agent-output] Skipping output processing: missing location_id agent_id=%s", agent_id)
        return

    text = extract_response_text(payload)
    if not text:
        return

    output_matches = list(OUTPUT_PATTERN.finditer(text))
    patch_matches = list(PATCH_PATTERN.finditer(text))

    try:
        for match in output_matches:
            attrs = parse_attrs(match.group("attrs"))
            output_type = attrs.get("type")
            markdown = clean_marker_text(match.group("body"))
            if markdown:
                upsert_full_output(agent_id, location_id, output_type, markdown, session_id)
                logger.info(
                    "[agent-output] Stored full output. agent_id=%s location_id=%s output_type=%s",
                    agent_id,
                    location_id,
                    output_type or "missing",
                )

        for match in patch_matches:
            attrs = parse_attrs(match.group("attrs"))
            output_type = attrs.get("type")
            target = attrs.get("target")
            mode = attrs.get("mode", "replace").lower()
            patch_markdown = clean_marker_text(match.group("body"))
            if target and patch_markdown:
                apply_output_patch(agent_id, location_id, output_type, target, mode, patch_markdown, session_id)
                logger.info(
                    "[agent-output] Applied output patch. agent_id=%s location_id=%s target=%s mode=%s",
                    agent_id,
                    location_id,
                    target,
                    mode,
                )
    except Exception:
        logger.exception("[agent-output] Failed to process agent output. agent_id=%s location_id=%s", agent_id, location_id)
