import hashlib
import hmac
import json
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.security import b64url_decode, b64url_encode, create_app_session
from app.db.session import db_connection
from app.schemas.ghl import GhlSessionResponse
from app.services.agents import get_agent_histories
from app.services.connections import find_connection_for_context
from app.services.demo_account import build_demo_context, ensure_demo_account
from app.services.workflow import build_workflow_status

router = APIRouter()
logger = get_logger()
DIRECT_DEV_LOGIN_TTL_SECONDS = 60 * 60 * 8


class DirectDevLoginRequest(BaseModel):
    username: str
    password: str


class DirectDevLoginResponse(BaseModel):
    loginToken: str


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


def _create_direct_dev_login_token(username: str, settings: Settings) -> str:
    if not settings.app_session_secret:
        raise ValueError("App session secret is not configured.")

    now = int(time.time())
    payload = {
        "purpose": "direct_dev_login",
        "username": username,
        "iat": now,
        "exp": now + DIRECT_DEV_LOGIN_TTL_SECONDS,
    }
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_part = b64url_encode(payload_json)
    signature = hmac.new(
        settings.app_session_secret.encode("utf-8"),
        payload_part.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return f"{payload_part}.{b64url_encode(signature)}"


def _verify_direct_dev_login_token(token: str, settings: Settings) -> dict:
    if not settings.app_session_secret:
        raise HTTPException(status_code=500, detail="App session secret is not configured.")

    try:
        payload_part, signature_part = token.split(".", 1)
        expected_signature = hmac.new(
            settings.app_session_secret.encode("utf-8"),
            payload_part.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        provided_signature = b64url_decode(signature_part)

        if not hmac.compare_digest(expected_signature, provided_signature):
            raise ValueError("Invalid signature.")

        payload = json.loads(b64url_decode(payload_part).decode("utf-8"))
        if payload.get("purpose") != "direct_dev_login":
            raise ValueError("Invalid token purpose.")
        if int(payload.get("exp", 0)) < int(time.time()):
            raise ValueError("Expired token.")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid direct dev login.")

    return payload


def _require_direct_dev_login(request: Request, settings: Settings) -> None:
    token = request.headers.get("X-Direct-Dev-Login")
    if not token:
        raise HTTPException(status_code=401, detail="Direct dev login is required.")

    _verify_direct_dev_login_token(token, settings)


@router.post("/dev/login", response_model=DirectDevLoginResponse)
async def create_direct_dev_login(payload: DirectDevLoginRequest):
    settings = get_settings()

    if not settings.direct_dev_session_enabled:
        raise HTTPException(status_code=404, detail="Direct dev session is disabled.")

    if not settings.app_session_secret:
        raise HTTPException(status_code=500, detail="App session secret is not configured.")

    if not settings.direct_dev_login_username or not settings.direct_dev_login_password:
        raise HTTPException(
            status_code=500,
            detail="Direct dev login is not configured. Set DIRECT_DEV_LOGIN_USERNAME and DIRECT_DEV_LOGIN_PASSWORD.",
        )

    username_ok = hmac.compare_digest(payload.username.strip(), settings.direct_dev_login_username)
    password_ok = hmac.compare_digest(payload.password, settings.direct_dev_login_password)
    if not username_ok or not password_ok:
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    return {"loginToken": _create_direct_dev_login_token(settings.direct_dev_login_username, settings)}


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

    _require_direct_dev_login(request, settings)

    is_demo_account = settings.direct_dev_account == "demo"
    if is_demo_account:
        context = build_demo_context()
    else:
        context = _env_context(settings)

    active_location = context.get("activeLocation")

    logger.info(
        "[direct-session:%s] Direct context prepared. company_id=%s active_location=%s user_id=%s email_present=%s source=%s",
        request_id,
        context.get("companyId") or "missing",
        active_location or "missing",
        context.get("userId") or "missing",
        bool(context.get("email")),
        "local_demo" if is_demo_account else "env",
    )

    if not context.get("companyId") or not active_location or not context.get("userId"):
        raise HTTPException(
            status_code=500,
            detail="Direct dev context is incomplete. Set DIRECT_DEV_COMPANY_ID, DIRECT_DEV_LOCATION_ID, and DIRECT_DEV_USER_ID.",
        )

    with db_connection() as conn:
        cursor = conn.cursor()
        try:
            if is_demo_account:
                ensure_demo_account(cursor, context, request_id)
                conn.commit()
            connection = find_connection_for_context(context, request_id=request_id, cursor=cursor)
            if not connection:
                raise HTTPException(status_code=403, detail="The direct dev account has not installed Ascala.")
            histories = get_agent_histories(context, validate_install=False, cursor=cursor)
            workflow_status = build_workflow_status(active_location, cursor=cursor)
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
        "workflowStatus": workflow_status,
    }
