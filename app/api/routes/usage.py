from fastapi import APIRouter, Header

from app.core.security import get_authorization_token, verify_app_session
from app.services.token_usage import build_token_usage_summary

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("/tokens")
async def token_usage_summary(authorization: str | None = Header(default=None)):
    session = verify_app_session(get_authorization_token(authorization))
    return build_token_usage_summary(session)
