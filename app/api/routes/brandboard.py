from fastapi import APIRouter, Header
from pydantic import BaseModel, Field

from app.brandboard.service import generate_brandboard, get_brandboard
from app.core.security import get_authorization_token, verify_app_session
from app.services.workflow import build_workflow_status

router = APIRouter(prefix="/brandboard", tags=["brandboard"])


class BrandBoardGenerateRequest(BaseModel):
    instruction: str = Field(default="", max_length=2000)


@router.get("")
async def fetch_brandboard(authorization: str | None = Header(default=None)):
    session = verify_app_session(get_authorization_token(authorization))
    result = get_brandboard(session)
    location_id = session.get("activeLocation") or session.get("locationId")
    if location_id:
        result["workflowStatus"] = build_workflow_status(location_id)
    return result


@router.post("/generate")
async def create_brandboard(
    payload: BrandBoardGenerateRequest | None = None,
    authorization: str | None = Header(default=None),
):
    session = verify_app_session(get_authorization_token(authorization))
    result = generate_brandboard(session, instruction=(payload.instruction if payload else ""))
    location_id = session.get("activeLocation") or session.get("locationId")
    if location_id:
        result["workflowStatus"] = build_workflow_status(location_id)
    return result
