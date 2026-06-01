import json

from fastapi import APIRouter, File, Header, Response, UploadFile

from app.core.security import get_authorization_token, verify_app_session
from app.db.session import db_connection
from app.escouade.service import require_location_id
from app.uply.service import build_uply_csv, get_location_access_token

router = APIRouter(prefix="/uply", tags=["uply"])


def get_session_context(authorization: str | None) -> dict:
    return verify_app_session(get_authorization_token(authorization))


@router.post("/social-planner/prepare")
async def prepare_social_planner_csv(
    schedule_file: UploadFile = File(...),
    media_zip: UploadFile = File(...),
    authorization: str | None = Header(default=None),
):
    session_context = get_session_context(authorization)
    location_id = require_location_id(session_context)
    with db_connection() as conn:
        cursor = conn.cursor()
        try:
            token = get_location_access_token(cursor, location_id)
        finally:
            cursor.close()

    prepared = await build_uply_csv(
        schedule_file=schedule_file,
        media_zip=media_zip,
        token=token,
    )

    return Response(
        content=prepared.content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{prepared.filename}"',
            "X-Uply-Summary": json.dumps(prepared.summary),
        },
    )
