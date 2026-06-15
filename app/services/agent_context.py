import json
import re
from typing import Any

from app.db.schema import ensure_agent_outputs_tables
from app.db.schema import ensure_brandboard_outputs_table


OUTPUT_TABLES = {
    "molly": "molly_outputs",
    "brandy": "brandy_outputs",
    "brandboard": "brandboard_outputs",
    "sacha": "sacha_outputs",
}

UPSTREAM_CONTEXT_PLAN = {
    "molly": {},
    "brandy": {
        "molly": [
            "positioning",
            "transformation",
            "ideal_client",
            "deep_psychology",
            "buyer_behavior",
            "language",
            "content_engine",
            "hook",
            "objection",
        ],
    },
    "sacha": {
        "brandboard": [
            "brand_foundation",
            "audience_messaging",
            "voice_tone_system",
            "content_application_system",
            "color_system",
            "typography_system",
            "button_system",
            "imagery_system",
        ],
        "molly": [
            "positioning",
            "ideal_client",
            "buyer_behavior",
            "language",
            "content_pillars",
            "content_engine",
            "offer_alignment",
            "hook",
            "objection",
        ],
        "brandy": [
            "brand_summary",
            "brand_voice",
            "voice_dna",
            "belief",
            "tone",
            "style",
            "language",
            "audience",
            "guardrails",
            "voice_engine",
        ],
    },
}

SECTION_CHAR_LIMIT = 1400
EXCERPT_CHAR_LIMIT = 4200


def normalize_key(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower())
    return normalized.strip("_")


def coerce_json(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def select_sections(structured_output: dict, desired_keys: list[str]) -> dict[str, str]:
    sections = structured_output.get("sections") if isinstance(structured_output, dict) else None
    if not isinstance(sections, dict) and isinstance(structured_output, dict):
        sections = structured_output
    if not isinstance(sections, dict):
        return {}

    selected = {}
    normalized_desired = [normalize_key(key) for key in desired_keys]
    for section_key, section_value in sections.items():
        normalized_section_key = normalize_key(section_key)
        if not any(desired_key in normalized_section_key for desired_key in normalized_desired):
            continue

        if isinstance(section_value, dict):
            content = str(section_value.get("content") or "")
            heading = section_value.get("heading") or section_key
            if not content.strip():
                content = json.dumps(section_value, ensure_ascii=False, default=str)
        else:
            content = str(section_value)
            heading = section_key

        if content.strip():
            selected[normalize_key(str(heading))] = content.strip()[:SECTION_CHAR_LIMIT]

    return selected


def load_agent_output(cursor, location_id: str, agent_id: str, desired_keys: list[str]) -> dict[str, Any] | None:
    table_name = OUTPUT_TABLES[agent_id]
    cursor.execute(
        f"""
        SELECT final_output, structured_output, output_type, version, updated_at
        FROM {table_name}
        WHERE location_id = %s
        LIMIT 1
        """,
        (location_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None

    final_output, raw_structured_output, output_type, version, updated_at = row
    structured_output = coerce_json(raw_structured_output)
    selected_sections = select_sections(structured_output, desired_keys)
    summary = {"sections": selected_sections} if selected_sections else {"excerpt": (final_output or "")[:EXCERPT_CHAR_LIMIT]}

    return {
        "agentId": agent_id,
        "outputType": output_type,
        "version": version,
        "updatedAt": updated_at.isoformat() if hasattr(updated_at, "isoformat") else updated_at,
        "summary": summary,
    }


def build_upstream_context(cursor, location_id: str | None, agent_id: str) -> dict[str, Any]:
    if not location_id:
        return {}

    context_plan = UPSTREAM_CONTEXT_PLAN.get(agent_id, {})
    if not context_plan:
        return {}

    ensure_agent_outputs_tables(cursor)
    ensure_brandboard_outputs_table(cursor)
    context = {}
    for upstream_agent_id, desired_keys in context_plan.items():
        output = load_agent_output(cursor, location_id, upstream_agent_id, desired_keys)
        if output:
            context[upstream_agent_id] = output

    return context


def format_upstream_context(context: dict[str, Any]) -> str:
    if not context:
        return ""

    blocks = []
    for agent_id, payload in context.items():
        summary = payload.get("summary") or {}
        lines = [
            f"## {agent_id.title()} Context",
            f"Output type: {payload.get('outputType') or 'unknown'}",
            f"Version: {payload.get('version') or 'unknown'}",
        ]

        sections = summary.get("sections")
        if isinstance(sections, dict) and sections:
            for heading, content in sections.items():
                lines.append(f"\n### {heading.replace('_', ' ').title()}\n{content}")
        elif summary.get("excerpt"):
            lines.append(f"\n### Excerpt\n{summary['excerpt']}")

        blocks.append("\n".join(lines).strip())

    return "\n\n---\n\n".join(blocks)
