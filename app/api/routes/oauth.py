import time
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.core.config import get_settings
from app.core.logging import get_logger, safe_log_dict
from app.services.oauth import exchange_authorization_code, store_oauth_token

router = APIRouter()
logger = get_logger()


@router.get("/oauth-callback")
async def oauth_callback(request: Request):
    request_id = str(uuid.uuid4())
    started_at = time.time()
    raw_query_params = dict(request.query_params)

    logger.info(
        "[oauth-callback:%s] Callback received. url=%s query_params=%s client_host=%s user_agent=%s referer=%s",
        request_id,
        str(request.url.replace(query="")),
        safe_log_dict(raw_query_params),
        request.client.host if request.client else "missing",
        request.headers.get("user-agent", "missing"),
        request.headers.get("referer", "missing"),
    )

    code = request.query_params.get("code")
    if not code:
        logger.warning("[oauth-callback:%s] Authorization code missing from callback.", request_id)
        return {"error": "Authorization code not found in the request."}

    data = exchange_authorization_code(code, request_id)
    if not data:
        return {"error": "Failed to obtain access token."}

    location_id = data.get("locationId")
    company_id = data.get("companyId")
    is_bulk_installation = data.get("isBulkInstallation")

    logger.info(
        "[oauth-callback:%s] Token response parsed. keys=%s safe_response=%s",
        request_id,
        sorted(data.keys()),
        safe_log_dict(data),
    )
    logger.info(
        "[oauth-callback:%s] Install context from token response. company_id=%s location_id=%s user_id=%s user_type=%s is_bulk_installation=%s scopes_present=%s",
        request_id,
        company_id or "missing",
        location_id or "missing",
        data.get("userId") or "missing",
        data.get("userType") or "missing",
        is_bulk_installation,
        bool(data.get("scope")),
    )

    if not location_id:
        logger.warning(
            "[oauth-callback:%s] No locationId returned by HighLevel token response. This usually means HighLevel treated the install as company/bulk/agency-level, or the OAuth app/install flow did not provide a sub-account location context. company_id=%s is_bulk_installation=%s user_type=%s",
            request_id,
            company_id or "missing",
            is_bulk_installation,
            data.get("userType") or "missing",
        )

    try:
        store_oauth_token(data, request_id, started_at)
    except ValueError as exc:
        logger.error("[oauth-callback:%s] %s", request_id, exc)
        return {"error": str(exc)}
    except Exception as exc:
        return {"error": str(exc)}

    logger.info("[oauth-callback:%s] Redirecting user to HighLevel app.", request_id)
    return RedirectResponse(url=get_settings().oauth_redirect_url, status_code=302)
