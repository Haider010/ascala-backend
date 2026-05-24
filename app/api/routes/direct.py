import time
import uuid

from fastapi import APIRouter, HTTPException, Request

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.security import create_app_session
from app.db.schema import ensure_installed_locations_table
from app.db.session import db_connection
from app.schemas.ghl import GhlSessionResponse
from app.services.agents import get_agent_histories
from app.services.connections import find_connection_for_context

router = APIRouter()
logger = get_logger()


def _apply_env_overrides(context: dict, settings: Settings) -> dict:
    overrides = {
        "companyId": settings.direct_dev_company_id,
        "activeLocation": settings.direct_dev_location_id,
        "userId": settings.direct_dev_user_id,
        "userName": settings.direct_dev_user_name,
        "email": settings.direct_dev_email,
        "role": settings.direct_dev_role,
        "type": settings.direct_dev_context_type,
        "isAgencyOwner": settings.direct_dev_is_agency_owner,
        "appStatus": settings.direct_dev_app_status,
        "versionId": settings.direct_dev_version_id,
    }
    return {**context, **{key: value for key, value in overrides.items() if value not in (None, "")}}


def _env_context(settings: Settings) -> dict:
    return _apply_env_overrides({}, settings)


def _load_default_direct_context(request_id: str) -> dict:
    with db_connection() as conn:
        cursor = conn.cursor()
        try:
            ensure_installed_locations_table(cursor)
            cursor.execute(
                """
                SELECT
                    c.company_id,
                    COALESCE(l.user_id, c.user_id),
                    l.location_id,
                    l.user_name,
                    l.email,
                    l.role,
                    l.context_type,
                    l.is_agency_owner,
                    l.app_status,
                    l.version_id
                FROM ascala_connections c
                LEFT JOIN ascala_installed_locations l ON l.connection_id = c.id
                ORDER BY l.last_seen_at DESC NULLS LAST, c.updated_at DESC NULLS LAST, c.created_at DESC NULLS LAST
                LIMIT 1
                """
            )
            row = cursor.fetchone()
        finally:
            cursor.close()

    if not row:
        logger.warning("[direct-session:%s] No Ascala connection found for direct dev session.", request_id)
        return {}

    return {
        "companyId": row[0],
        "userId": row[1],
        "activeLocation": row[2],
        "userName": row[3],
        "email": row[4],
        "role": row[5],
        "type": row[6],
        "isAgencyOwner": row[7],
        "appStatus": row[8],
        "versionId": row[9],
    }


@router.post("/dev/session", response_model=GhlSessionResponse)
async def create_direct_dev_session(request: Request):
    request_id = str(uuid.uuid4())
    started_at = time.time()
    settings = get_settings()

    logger.info(
        "[direct-session:%s] Request received. origin=%s referer=%s user_agent=%s enabled=%s",
        request_id,
        request.headers.get("origin", "missing"),
        request.headers.get("referer", "missing"),
        request.headers.get("user-agent", "missing"),
        settings.direct_dev_session_enabled,
    )

    if not settings.direct_dev_session_enabled:
        raise HTTPException(status_code=404, detail="Direct dev session is disabled.")

    if not settings.app_session_secret:
        logger.error("[direct-session:%s] Missing app session secret environment variable.", request_id)
        raise HTTPException(status_code=500, detail="App session secret is not configured.")

    context = _env_context(settings)
    if not context.get("companyId") or not context.get("activeLocation") or not context.get("userId"):
        logger.info("[direct-session:%s] Direct env context is incomplete. Falling back to database defaults.", request_id)
        context = _apply_env_overrides(_load_default_direct_context(request_id), settings)
    active_location = context.get("activeLocation")

    logger.info(
        "[direct-session:%s] Direct context prepared. company_id=%s active_location=%s user_id=%s email_present=%s source=db_with_env_overrides",
        request_id,
        context.get("companyId") or "missing",
        active_location or "missing",
        context.get("userId") or "missing",
        bool(context.get("email")),
    )

    if not context.get("companyId") or not active_location or not context.get("userId"):
        raise HTTPException(
            status_code=500,
            detail="Direct dev context is incomplete. Set DIRECT_DEV_COMPANY_ID, DIRECT_DEV_LOCATION_ID, and DIRECT_DEV_USER_ID.",
        )

    with db_connection() as conn:
        cursor = conn.cursor()
        try:
            connection = find_connection_for_context(context, request_id=request_id, cursor=cursor)
            if not connection:
                raise HTTPException(status_code=403, detail="The direct dev account has not installed Ascala.")
            histories = get_agent_histories(context, validate_install=False, cursor=cursor)
        finally:
            cursor.close()

    session_token = create_app_session(context)
    storage_scope = ".".join([active_location, context.get("userId") or context.get("email") or "unknown-user"])

    logger.info(
        "[direct-session:%s] Direct dev session created successfully. company_id=%s active_location=%s user_id=%s duration_ms=%s",
        request_id,
        context.get("companyId"),
        active_location,
        context.get("userId"),
        int((time.time() - started_at) * 1000),
    )

    return {
        "sessionToken": session_token,
        "userId": context.get("userId"),
        "companyId": context.get("companyId"),
        "activeLocation": active_location,
        "role": context.get("role"),
        "type": context.get("type"),
        "userName": context.get("userName"),
        "email": context.get("email"),
        "isAgencyOwner": context.get("isAgencyOwner"),
        "storageScope": storage_scope,
        "histories": histories,
    }
