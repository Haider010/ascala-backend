import time
import uuid

from fastapi import APIRouter, HTTPException, Request

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import create_app_session, decrypt_cryptojs_aes
from app.schemas.ghl import GhlSessionRequest, GhlSessionResponse
from app.services.agents import get_agent_histories
from app.services.connections import find_connection_for_context, upsert_installed_location

router = APIRouter()
logger = get_logger()


@router.post("/ghl/session", response_model=GhlSessionResponse)
async def create_ghl_session(payload: GhlSessionRequest, request: Request):
    request_id = str(uuid.uuid4())
    started_at = time.time()
    settings = get_settings()

    logger.info(
        "[ghl-session:%s] Request received. origin=%s referer=%s user_agent=%s",
        request_id,
        request.headers.get("origin", "missing"),
        request.headers.get("referer", "missing"),
        request.headers.get("user-agent", "missing"),
    )

    if not settings.ghl_app_shared_secret:
        logger.error("[ghl-session:%s] Missing GHL app shared secret environment variable.", request_id)
        raise HTTPException(status_code=500, detail="GHL app shared secret is not configured.")

    if not settings.app_session_secret:
        logger.error("[ghl-session:%s] Missing app session secret environment variable.", request_id)
        raise HTTPException(status_code=500, detail="App session secret is not configured.")

    logger.info(
        "[ghl-session:%s] Request payload inspected. encryptedData_length=%s query_param_keys=%s",
        request_id,
        len(payload.encryptedData),
        sorted(payload.queryParams.keys()) if isinstance(payload.queryParams, dict) else "non-dict",
    )

    try:
        logger.info("[ghl-session:%s] Starting GHL encrypted context decryption.", request_id)
        context = decrypt_cryptojs_aes(payload.encryptedData, settings.ghl_app_shared_secret)
        logger.info(
            "[ghl-session:%s] Decryption succeeded. context_keys=%s user_id=%s company_id=%s active_location=%s role=%s type=%s email_present=%s app_status=%s version_id=%s",
            request_id,
            sorted(context.keys()) if isinstance(context, dict) else "non-dict",
            context.get("userId") if isinstance(context, dict) else "missing",
            context.get("companyId") if isinstance(context, dict) else "missing",
            context.get("activeLocation") if isinstance(context, dict) else "missing",
            context.get("role") if isinstance(context, dict) else "missing",
            context.get("type") if isinstance(context, dict) else "missing",
            bool(context.get("email")) if isinstance(context, dict) else False,
            context.get("appStatus") if isinstance(context, dict) else "missing",
            context.get("versionId") if isinstance(context, dict) else "missing",
        )
    except Exception:
        logger.exception("[ghl-session:%s] Unable to decrypt GHL session context.", request_id)
        raise HTTPException(status_code=400, detail="Unable to decrypt GHL session context.")

    if not isinstance(context, dict):
        logger.warning("[ghl-session:%s] Decrypted context is not an object. context_type=%s", request_id, type(context).__name__)
        raise HTTPException(status_code=400, detail="Decrypted GHL session context is invalid.")

    connection = find_connection_for_context(context, request_id=request_id)
    if not connection:
        logger.warning(
            "[ghl-session:%s] Blocking session: no matching Ascala install. user_id=%s company_id=%s active_location=%s",
            request_id,
            context.get("userId") or "missing",
            context.get("companyId") or "missing",
            context.get("activeLocation") or "missing",
        )
        raise HTTPException(status_code=403, detail="This GHL account has not installed Ascala.")

    upsert_installed_location(context, connection, request_id)

    try:
        logger.info("[ghl-session:%s] Creating signed Ascala app session token.", request_id)
        session_token = create_app_session(context)
    except Exception:
        logger.exception("[ghl-session:%s] Failed to create signed Ascala app session token.", request_id)
        raise HTTPException(status_code=500, detail="Unable to create app session.")

    active_location = context.get("activeLocation") or connection.get("locationId")
    storage_scope = ".".join([
        active_location or "agency",
        context.get("userId") or context.get("email") or "unknown-user",
    ])
    histories = get_agent_histories({**context, "activeLocation": active_location}, validate_install=False)

    duration_ms = int((time.time() - started_at) * 1000)
    logger.info(
        "[ghl-session:%s] Session created successfully. active_location=%s user_id=%s company_id=%s connection_id=%s storage_scope=%s duration_ms=%s",
        request_id,
        active_location or "missing",
        context.get("userId") or "missing",
        context.get("companyId") or "missing",
        connection.get("id"),
        storage_scope,
        duration_ms,
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
