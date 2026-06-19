import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.common import build_chat_llm, read_platform_knowledge, read_prompt
from app.brandboard.schemas import BrandBoardDocument
from app.core.config import get_settings
from app.db.schema import ensure_agent_outputs_tables, ensure_brandboard_outputs_table
from app.db.session import db_connection
from app.services.connections import find_connection_for_context
from app.services.token_usage import record_token_usage

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "master.md"
OUTPUT_TYPE = "brand_guidelines_100x"


def require_location_id(session: dict[str, Any]) -> str:
    if not find_connection_for_context(session):
        raise HTTPException(status_code=403, detail="This app session is not linked to an installed account.")

    location_id = session.get("activeLocation") or session.get("locationId")
    if not location_id:
        raise HTTPException(status_code=400, detail="Unable to determine account location.")

    return location_id


def _coerce_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _load_source_output(cursor, table_name: str, location_id: str) -> dict[str, Any] | None:
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

    final_output, structured_output, output_type, version, updated_at = row
    return {
        "finalOutput": final_output or "",
        "structuredOutput": _coerce_json(structured_output),
        "outputType": output_type,
        "version": version,
        "updatedAt": updated_at,
    }


def _load_brandboard_sources(location_id: str) -> dict[str, Any]:
    with db_connection() as conn:
        cursor = conn.cursor()
        try:
            ensure_agent_outputs_tables(cursor)
            molly = _load_source_output(cursor, "molly_outputs", location_id)
            brandy = _load_source_output(cursor, "brandy_outputs", location_id)
        finally:
            cursor.close()

    missing = []
    if not molly:
        missing.append("Molly")
    if not brandy:
        missing.append("Brandy")

    if missing:
        raise HTTPException(
            status_code=409,
            detail=f"BrandBoard needs completed {' and '.join(missing)} output first.",
        )

    return {"molly": molly, "brandy": brandy}


def _source_text(label: str, source: dict[str, Any], char_limit: int = 18000) -> str:
    structured = source.get("structuredOutput") or {}
    if structured:
        content = json.dumps(structured, ensure_ascii=False, default=str)
    else:
        content = source.get("finalOutput") or ""

    return f"## {label}\nOutput type: {source.get('outputType') or 'unknown'}\n\n{content[:char_limit]}"


def _build_messages(sources: dict[str, Any], instruction: str = "") -> list:
    context = "\n\n---\n\n".join([
        _source_text("Molly Audience Intelligence", sources["molly"]),
        _source_text("Brandy Brand Strategy", sources["brandy"]),
    ])
    user_instruction = instruction.strip()[:2000]
    instruction_block = ""
    if user_instruction:
        instruction_block = (
            "\n\nAdditional user instruction for this regeneration:\n"
            f"{user_instruction}\n\n"
            "Apply this instruction only within the existing brand guidelines schema. "
            "Do not change, omit, rename, or add output sections. Keep the same structured format exactly."
        )

    return [
        SystemMessage(content="\n\n".join([read_prompt(PROMPT_PATH), read_platform_knowledge()])),
        HumanMessage(
            content=(
                "Generate the BrandBoard 100X brand guidelines JSON from the account context below. "
                "Use Molly for audience intelligence and Brandy for brand voice, positioning, and tone. "
                "Return a complete, premium, reusable brand guideline document. "
                "Skip logo-system recommendations because no real logo assets are provided."
                f"{instruction_block}\n\n"
                f"{context}"
            )
        ),
    ]


def _invoke_structured_brandboard(
    session: dict[str, Any],
    sources: dict[str, Any],
    instruction: str = "",
) -> BrandBoardDocument:
    settings = get_settings()
    llm = build_chat_llm(settings.brandboard_model)
    structured_llm = llm.with_structured_output(BrandBoardDocument, include_raw=True)
    response = structured_llm.invoke(_build_messages(sources, instruction))

    raw_response = response.get("raw") if isinstance(response, dict) else None
    if raw_response is not None:
        record_token_usage(
            session=session,
            agent_id="brandboard",
            model=settings.brandboard_model,
            response=raw_response,
        )

    parsing_error = response.get("parsing_error") if isinstance(response, dict) else None
    if parsing_error:
        raise HTTPException(status_code=502, detail=f"BrandBoard output failed schema validation: {parsing_error}")

    parsed = response.get("parsed") if isinstance(response, dict) else response
    if isinstance(parsed, BrandBoardDocument):
        return parsed
    if isinstance(parsed, dict):
        return BrandBoardDocument.model_validate(parsed)

    raise HTTPException(status_code=502, detail="BrandBoard output failed schema validation.")


def _serialize_output(document: BrandBoardDocument) -> dict[str, Any]:
    return document.model_dump(mode="json")


def _upsert_brandboard(
    location_id: str,
    document: BrandBoardDocument,
    sources: dict[str, Any],
    instruction: str = "",
) -> dict[str, Any]:
    output_json = _serialize_output(document)
    theme = output_json.get("theme_selection", {}).get("theme")
    final_output = json.dumps(output_json, ensure_ascii=False)
    metadata = {
        "sourceOutputs": {
            "molly": {
                "outputType": sources["molly"].get("outputType"),
                "version": sources["molly"].get("version"),
            },
            "brandy": {
                "outputType": sources["brandy"].get("outputType"),
                "version": sources["brandy"].get("version"),
            },
        }
    }
    if instruction.strip():
        metadata["generationInstruction"] = instruction.strip()[:2000]

    with db_connection() as conn:
        cursor = conn.cursor()
        try:
            ensure_brandboard_outputs_table(cursor)
            cursor.execute(
                """
                INSERT INTO brandboard_outputs (
                    location_id,
                    final_output,
                    structured_output,
                    output_type,
                    theme,
                    metadata,
                    source_molly_updated_at,
                    source_brandy_updated_at,
                    updated_at
                )
                VALUES (%s, %s, %s::jsonb, %s, %s, %s::jsonb, %s, %s, NOW())
                ON CONFLICT (location_id)
                DO UPDATE SET
                    final_output = EXCLUDED.final_output,
                    structured_output = EXCLUDED.structured_output,
                    output_type = EXCLUDED.output_type,
                    theme = EXCLUDED.theme,
                    metadata = EXCLUDED.metadata,
                    source_molly_updated_at = EXCLUDED.source_molly_updated_at,
                    source_brandy_updated_at = EXCLUDED.source_brandy_updated_at,
                    version = brandboard_outputs.version + 1,
                    updated_at = NOW()
                RETURNING structured_output, updated_at
                """,
                (
                    location_id,
                    final_output,
                    json.dumps(output_json, ensure_ascii=False),
                    OUTPUT_TYPE,
                    theme,
                    json.dumps(metadata, default=str),
                    sources["molly"].get("updatedAt"),
                    sources["brandy"].get("updatedAt"),
                ),
            )
            row = cursor.fetchone()
            conn.commit()
        finally:
            cursor.close()

    structured_output, updated_at = row
    return {
        "output": structured_output,
        "updatedAt": updated_at.isoformat() if hasattr(updated_at, "isoformat") else updated_at,
    }


def get_brandboard(session: dict[str, Any]) -> dict[str, Any]:
    location_id = require_location_id(session)
    with db_connection() as conn:
        cursor = conn.cursor()
        try:
            ensure_brandboard_outputs_table(cursor)
            cursor.execute(
                """
                SELECT structured_output, updated_at
                FROM brandboard_outputs
                WHERE location_id = %s
                LIMIT 1
                """,
                (location_id,),
            )
            row = cursor.fetchone()
        finally:
            cursor.close()

    if not row:
        return {"output": None, "updatedAt": None, "generated": False}

    structured_output, updated_at = row
    return {
        "output": structured_output,
        "updatedAt": updated_at.isoformat() if hasattr(updated_at, "isoformat") else updated_at,
        "generated": True,
    }


def generate_brandboard(session: dict[str, Any], instruction: str = "") -> dict[str, Any]:
    location_id = require_location_id(session)
    sources = _load_brandboard_sources(location_id)
    document = _invoke_structured_brandboard(session, sources, instruction)
    saved = _upsert_brandboard(location_id, document, sources, instruction)
    return {
        **saved,
        "generated": True,
    }
