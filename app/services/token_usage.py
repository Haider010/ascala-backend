import json
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.db.schema import ensure_token_usage_table
from app.db.session import db_connection

logger = get_logger()


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _metadata_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def extract_token_usage(response: Any) -> dict[str, Any] | None:
    usage_metadata = _metadata_dict(getattr(response, "usage_metadata", None))
    response_metadata = _metadata_dict(getattr(response, "response_metadata", None))
    token_usage = _metadata_dict(response_metadata.get("token_usage") or response_metadata.get("usage"))

    input_tokens = (
        usage_metadata.get("input_tokens")
        or usage_metadata.get("prompt_tokens")
        or token_usage.get("prompt_tokens")
        or token_usage.get("input_tokens")
    )
    output_tokens = (
        usage_metadata.get("output_tokens")
        or usage_metadata.get("completion_tokens")
        or token_usage.get("completion_tokens")
        or token_usage.get("output_tokens")
    )
    total_tokens = (
        usage_metadata.get("total_tokens")
        or token_usage.get("total_tokens")
        or token_usage.get("total")
    )
    input_token_details = _metadata_dict(usage_metadata.get("input_token_details") or usage_metadata.get("prompt_tokens_details"))
    prompt_token_details = _metadata_dict(token_usage.get("prompt_tokens_details") or token_usage.get("input_token_details"))
    cached_input_tokens = (
        input_token_details.get("cache_read")
        or input_token_details.get("cached_tokens")
        or prompt_token_details.get("cached_tokens")
        or prompt_token_details.get("cache_read")
    )

    normalized = {
        "input_tokens": _to_int(input_tokens),
        "cached_input_tokens": _to_int(cached_input_tokens),
        "output_tokens": _to_int(output_tokens),
        "total_tokens": _to_int(total_tokens),
        "usage_metadata": usage_metadata,
        "response_metadata_usage": token_usage,
        "response_id": response_metadata.get("id") or response_metadata.get("response_id"),
    }
    normalized["cached_input_tokens"] = min(normalized["cached_input_tokens"], normalized["input_tokens"])
    normalized["fresh_input_tokens"] = max(normalized["input_tokens"] - normalized["cached_input_tokens"], 0)
    if not normalized["total_tokens"]:
        normalized["total_tokens"] = normalized["input_tokens"] + normalized["output_tokens"]

    if not any((normalized["input_tokens"], normalized["output_tokens"], normalized["total_tokens"])):
        return None

    return normalized


def record_token_usage(
    *,
    session: dict[str, Any] | None,
    agent_id: str,
    model: str | None,
    response: Any,
    metadata: dict[str, Any] | None = None,
) -> None:
    usage = extract_token_usage(response)
    if not usage:
        logger.info("[token-usage] No token usage metadata found. agent_id=%s model=%s", agent_id, model or "unknown")
        return

    session = session or {}
    location_id = session.get("activeLocation") or session.get("locationId")
    if not location_id:
        logger.info("[token-usage] Skipping token usage: missing location_id agent_id=%s", agent_id)
        return

    user_id = session.get("userId") or session.get("user_id") or "unknown"
    company_id = session.get("companyId") or session.get("company_id")
    usage_month = datetime.now(UTC).date().replace(day=1)
    last_usage_metadata = {
        **usage,
        "metadata": metadata or {},
        "recorded_at": datetime.now(UTC).isoformat(),
    }

    with db_connection() as conn:
        cursor = conn.cursor()
        try:
            ensure_token_usage_table(cursor)
            cursor.execute(
                """
                INSERT INTO ascala_token_usage_monthly (
                    usage_month,
                    company_id,
                    location_id,
                    user_id,
                    agent_id,
                    model,
                    input_tokens,
                    fresh_input_tokens,
                    cached_input_tokens,
                    output_tokens,
                    total_tokens,
                    call_count,
                    last_usage_metadata,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s::jsonb, NOW())
                ON CONFLICT (usage_month, location_id, user_id, agent_id)
                DO UPDATE SET
                    company_id = COALESCE(EXCLUDED.company_id, ascala_token_usage_monthly.company_id),
                    model = EXCLUDED.model,
                    input_tokens = ascala_token_usage_monthly.input_tokens + EXCLUDED.input_tokens,
                    fresh_input_tokens = ascala_token_usage_monthly.fresh_input_tokens + EXCLUDED.fresh_input_tokens,
                    cached_input_tokens = ascala_token_usage_monthly.cached_input_tokens + EXCLUDED.cached_input_tokens,
                    output_tokens = ascala_token_usage_monthly.output_tokens + EXCLUDED.output_tokens,
                    total_tokens = ascala_token_usage_monthly.total_tokens + EXCLUDED.total_tokens,
                    call_count = ascala_token_usage_monthly.call_count + 1,
                    last_usage_metadata = EXCLUDED.last_usage_metadata,
                    updated_at = NOW()
                """,
                (
                    usage_month,
                    company_id,
                    location_id,
                    user_id,
                    agent_id,
                    model,
                    usage["input_tokens"],
                    usage["fresh_input_tokens"],
                    usage["cached_input_tokens"],
                    usage["output_tokens"],
                    usage["total_tokens"],
                    json.dumps(last_usage_metadata, default=str),
                ),
            )
            conn.commit()
        except Exception:
            logger.exception("[token-usage] Failed to record token usage. agent_id=%s location_id=%s", agent_id, location_id)
        finally:
            cursor.close()


def empty_totals() -> dict[str, int]:
    return {
        "inputTokens": 0,
        "freshInputTokens": 0,
        "cachedInputTokens": 0,
        "outputTokens": 0,
        "totalTokens": 0,
        "callCount": 0,
    }


def build_token_usage_summary(session: dict[str, Any]) -> dict[str, Any]:
    location_id = session.get("activeLocation") or session.get("locationId")
    user_id = session.get("userId") or session.get("user_id") or "unknown"

    if not location_id:
        return {
            "currentMonth": empty_totals(),
            "allTime": empty_totals(),
            "months": [],
            "agents": [],
        }

    current_month = datetime.now(UTC).date().replace(day=1)

    with db_connection() as conn:
        cursor = conn.cursor()
        try:
            ensure_token_usage_table(cursor)
            cursor.execute(
                """
                SELECT
                    COALESCE(SUM(input_tokens), 0),
                    COALESCE(SUM(fresh_input_tokens), 0),
                    COALESCE(SUM(cached_input_tokens), 0),
                    COALESCE(SUM(output_tokens), 0),
                    COALESCE(SUM(total_tokens), 0),
                    COALESCE(SUM(call_count), 0)
                FROM ascala_token_usage_monthly
                WHERE location_id = %s
                  AND user_id = %s
                  AND usage_month = %s
                """,
                (location_id, user_id, current_month),
            )
            current_row = cursor.fetchone()

            cursor.execute(
                """
                SELECT
                    COALESCE(SUM(input_tokens), 0),
                    COALESCE(SUM(fresh_input_tokens), 0),
                    COALESCE(SUM(cached_input_tokens), 0),
                    COALESCE(SUM(output_tokens), 0),
                    COALESCE(SUM(total_tokens), 0),
                    COALESCE(SUM(call_count), 0)
                FROM ascala_token_usage_monthly
                WHERE location_id = %s
                  AND user_id = %s
                """,
                (location_id, user_id),
            )
            all_time_row = cursor.fetchone()

            cursor.execute(
                """
                SELECT
                    usage_month,
                    COALESCE(SUM(input_tokens), 0) AS input_tokens,
                    COALESCE(SUM(fresh_input_tokens), 0) AS fresh_input_tokens,
                    COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
                    COALESCE(SUM(output_tokens), 0) AS output_tokens,
                    COALESCE(SUM(total_tokens), 0) AS total_tokens,
                    COALESCE(SUM(call_count), 0) AS call_count
                FROM ascala_token_usage_monthly
                WHERE location_id = %s
                  AND user_id = %s
                GROUP BY usage_month
                ORDER BY usage_month DESC
                """,
                (location_id, user_id),
            )
            month_rows = cursor.fetchall()

            cursor.execute(
                """
                SELECT
                    agent_id,
                    model,
                    usage_month,
                    input_tokens,
                    fresh_input_tokens,
                    cached_input_tokens,
                    output_tokens,
                    total_tokens,
                    call_count,
                    updated_at
                FROM ascala_token_usage_monthly
                WHERE location_id = %s
                  AND user_id = %s
                ORDER BY usage_month DESC, agent_id ASC
                """,
                (location_id, user_id),
            )
            agent_rows = cursor.fetchall()
        finally:
            cursor.close()

    def totals(row) -> dict[str, int]:
        return {
            "inputTokens": _to_int(row[0]) if row else 0,
            "freshInputTokens": _to_int(row[1]) if row else 0,
            "cachedInputTokens": _to_int(row[2]) if row else 0,
            "outputTokens": _to_int(row[3]) if row else 0,
            "totalTokens": _to_int(row[4]) if row else 0,
            "callCount": _to_int(row[5]) if row else 0,
        }

    return {
        "locationId": location_id,
        "userId": user_id,
        "currentMonthKey": current_month.isoformat()[:7],
        "currentMonth": totals(current_row),
        "allTime": totals(all_time_row),
        "months": [
            {
                "month": row[0].isoformat()[:7] if hasattr(row[0], "isoformat") else str(row[0])[:7],
                "inputTokens": _to_int(row[1]),
                "freshInputTokens": _to_int(row[2]),
                "cachedInputTokens": _to_int(row[3]),
                "outputTokens": _to_int(row[4]),
                "totalTokens": _to_int(row[5]),
                "callCount": _to_int(row[6]),
            }
            for row in month_rows
        ],
        "agents": [
            {
                "agentId": row[0],
                "model": row[1],
                "month": row[2].isoformat()[:7] if hasattr(row[2], "isoformat") else str(row[2])[:7],
                "inputTokens": _to_int(row[3]),
                "freshInputTokens": _to_int(row[4]),
                "cachedInputTokens": _to_int(row[5]),
                "outputTokens": _to_int(row[6]),
                "totalTokens": _to_int(row[7]),
                "callCount": _to_int(row[8]),
                "updatedAt": row[9].isoformat() if hasattr(row[9], "isoformat") else row[9],
            }
            for row in agent_rows
        ],
    }
