from uuid import UUID

from fastapi import APIRouter, Header, Response

from app.core.security import get_authorization_token, verify_app_session
from app.db.sqlalchemy import sqlalchemy_session
from app.escouade.graph import escouade_graph
from app.escouade.schemas.common import (
    BatchGenerateRequest,
    BatchResponse,
    CommandRequest,
    EscouadeOperationResponse,
    ItemActionRequest,
    ReviseRequest,
)
from app.escouade.service import approve_items, get_batch_or_404, handle_command, reopen_items, require_location_id, serialize_batch
from app.services.workflow import build_workflow_status

router = APIRouter(prefix="/escouade", tags=["escouade"])


def get_session_context(authorization: str | None) -> dict:
    return verify_app_session(get_authorization_token(authorization))


@router.post("/batch/generate", response_model=EscouadeOperationResponse)
async def generate_batch(payload: BatchGenerateRequest, authorization: str | None = Header(default=None)):
    session_context = get_session_context(authorization)
    location_id = require_location_id(session_context)
    state = escouade_graph.invoke({
        "action": "generate",
        "location_id": location_id,
        "member_type": payload.member_type,
        "batch_name": payload.batch_name,
        "source_type": payload.source_type,
        "source_label": payload.source_label,
        "filters": payload.filters.model_dump(),
        "conversation_history": payload.conversation_history,
        "current_instruction": payload.message,
    })

    with sqlalchemy_session() as db:
        batch = get_batch_or_404(db, UUID(state["batch_id"]), location_id)
        return {
            "batch": serialize_batch(batch),
            "message": state.get("message") or "Batch generated and saved as drafts.",
            "workflowStatus": build_workflow_status(location_id),
        }


@router.post("/batch/command", response_model=EscouadeOperationResponse)
async def command_batch(payload: CommandRequest, authorization: str | None = Header(default=None)):
    session_context = get_session_context(authorization)
    location_id = require_location_id(session_context)
    with sqlalchemy_session() as db:
        batch, message, _filename = handle_command(
            db,
            location_id,
            payload.batch_id,
            payload.message,
            payload.conversation_history,
        )
        return {
            "batch": serialize_batch(batch),
            "message": message,
            "workflowStatus": build_workflow_status(location_id),
        }


@router.post("/batch/revise", response_model=EscouadeOperationResponse)
async def revise_batch(payload: ReviseRequest, authorization: str | None = Header(default=None)):
    session_context = get_session_context(authorization)
    location_id = require_location_id(session_context)
    state = escouade_graph.invoke({
        "action": "revise",
        "location_id": location_id,
        "batch_id": str(payload.batch_id),
        "item_ids": [str(item_id) for item_id in payload.item_ids],
        "conversation_history": payload.conversation_history,
        "current_instruction": payload.instruction,
    })

    with sqlalchemy_session() as db:
        batch = get_batch_or_404(db, payload.batch_id, location_id)
        return {
            "batch": serialize_batch(batch),
            "message": state.get("message") or "Editable items revised and saved.",
            "workflowStatus": build_workflow_status(location_id),
        }


@router.post("/batch/approve", response_model=EscouadeOperationResponse)
async def approve_batch_items(payload: ItemActionRequest, authorization: str | None = Header(default=None)):
    session_context = get_session_context(authorization)
    location_id = require_location_id(session_context)
    with sqlalchemy_session() as db:
        batch = approve_items(db, location_id, payload.batch_id, payload.item_ids)
        return {
            "batch": serialize_batch(batch),
            "message": "Selected draft items approved and locked.",
            "workflowStatus": build_workflow_status(location_id),
        }


@router.post("/batch/reopen", response_model=EscouadeOperationResponse)
async def reopen_batch_items(payload: ItemActionRequest, authorization: str | None = Header(default=None)):
    session_context = get_session_context(authorization)
    location_id = require_location_id(session_context)
    with sqlalchemy_session() as db:
        batch = reopen_items(db, location_id, payload.batch_id, payload.item_ids)
        return {
            "batch": serialize_batch(batch),
            "message": "Selected approved items reopened for revision.",
            "workflowStatus": build_workflow_status(location_id),
        }


@router.get("/batch/export-csv")
async def export_batch_csv(batch_id: UUID, authorization: str | None = Header(default=None)):
    session_context = get_session_context(authorization)
    location_id = require_location_id(session_context)
    state = escouade_graph.invoke({
        "action": "export",
        "location_id": location_id,
        "batch_id": str(batch_id),
    })
    return Response(
        content=state["csv_content"],
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{state["csv_filename"]}"'},
    )


@router.get("/batch/{batch_id}", response_model=BatchResponse)
async def get_batch(batch_id: UUID, authorization: str | None = Header(default=None)):
    session_context = get_session_context(authorization)
    location_id = require_location_id(session_context)
    with sqlalchemy_session() as db:
        batch = get_batch_or_404(db, batch_id, location_id)
        return serialize_batch(batch)
