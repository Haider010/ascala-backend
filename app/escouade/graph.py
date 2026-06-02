from uuid import UUID

from fastapi import HTTPException
from langgraph.graph import END, StateGraph

from app.db.sqlalchemy import sqlalchemy_session
from app.escouade import service
from app.escouade.schemas.common import EscouadeBatchFilters
from app.escouade.state import EscouadeState


def load_context(state: EscouadeState) -> EscouadeState:
    with sqlalchemy_session() as db:
        state["knowledge_context"] = service.load_knowledge_context(db, state["location_id"])
        if state.get("member_type"):
            filters = state.get("filters") or {}
            state["production_brief"] = service.build_production_brief(state["knowledge_context"], state["member_type"], filters)
    return state


def parse_instruction(state: EscouadeState) -> EscouadeState:
    state["current_instruction"] = (state.get("current_instruction") or "").strip()
    return state


def strategy_review(state: EscouadeState) -> EscouadeState:
    if state.get("member_type"):
        state["strategy_review"] = service.build_strategy_review(
            state["member_type"],
            state.get("filters") or {},
            state.get("production_brief") or {},
        )
    return state


def filter_editable_items(state: EscouadeState) -> EscouadeState:
    with sqlalchemy_session() as db:
        editable, locked = service.filter_editable_items(
            db,
            state["location_id"],
            UUID(state["batch_id"]),
            [UUID(item_id) for item_id in state.get("item_ids", [])],
        )
        state["editable_item_ids"] = [str(item.id) for item in editable]
        state["locked_item_ids"] = [str(item.id) for item in locked]
        if locked:
            locked_labels = ", ".join(f"{item.post_id} ({item.status})" for item in locked)
            raise HTTPException(status_code=409, detail=f"Locked items cannot be revised. Reopen them first: {locked_labels}")
        if not editable:
            raise HTTPException(status_code=409, detail="No editable items were selected.")
    return state


def generate_batch(state: EscouadeState) -> EscouadeState:
    with sqlalchemy_session() as db:
        batch = service.generate_batch(
            db,
            state["location_id"],
            {
                "activeLocation": state["location_id"],
                "companyId": state.get("company_id"),
                "userId": state.get("user_id"),
            },
            state["member_type"],
            state.get("batch_name"),
            state.get("source_type"),
            state.get("source_label"),
            EscouadeBatchFilters.model_validate(state.get("filters") or {}),
            state["current_instruction"],
            state.get("conversation_history", []),
        )
        state["batch_id"] = str(batch.id)
        state["items"] = [item.content for item in batch.items]
        state["quality_note"] = batch.quality_note
    return state


def revise_items(state: EscouadeState) -> EscouadeState:
    with sqlalchemy_session() as db:
        batch, _locked = service.revise_items(
            db,
            state["location_id"],
            {
                "activeLocation": state["location_id"],
                "companyId": state.get("company_id"),
                "userId": state.get("user_id"),
            },
            UUID(state["batch_id"]),
            [UUID(item_id) for item_id in state.get("editable_item_ids", [])],
            state["current_instruction"],
            state.get("conversation_history", []),
        )
        state["items"] = [item.content for item in batch.items]
        state["quality_note"] = batch.quality_note
    return state


def quality_feedback(state: EscouadeState) -> EscouadeState:
    if state.get("quality_note"):
        state["message"] = state["quality_note"] or ""
    elif state.get("action") == "generate":
        state["message"] = "Batch generated and saved as drafts."
    elif state.get("action") == "revise":
        state["message"] = "Editable items revised and saved."
    return state


def export_csv(state: EscouadeState) -> EscouadeState:
    with sqlalchemy_session() as db:
        batch, csv_content, filename = service.export_approved_csv(db, state["location_id"], UUID(state["batch_id"]))
        state["member_type"] = batch.member_type
        state["csv_content"] = csv_content
        state["csv_filename"] = filename
        state["message"] = "Approved items exported."
    return state


def route_action(state: EscouadeState) -> str:
    action = state.get("action")
    if action == "generate":
        return "generate_batch"
    if action == "revise":
        return "filter_editable_items"
    if action == "export":
        return "export_csv"
    return END


def build_escouade_graph():
    graph = StateGraph(EscouadeState)
    graph.add_node("load_context", load_context)
    graph.add_node("parse_instruction", parse_instruction)
    graph.add_node("strategy_review", strategy_review)
    graph.add_node("filter_editable_items", filter_editable_items)
    graph.add_node("generate_batch", generate_batch)
    graph.add_node("revise_items", revise_items)
    graph.add_node("quality_feedback", quality_feedback)
    graph.add_node("export_csv", export_csv)

    graph.set_entry_point("load_context")
    graph.add_edge("load_context", "parse_instruction")
    graph.add_conditional_edges(
        "parse_instruction",
        route_action,
        {
            "generate_batch": "strategy_review",
            "filter_editable_items": "filter_editable_items",
            "export_csv": "export_csv",
            END: END,
        },
    )
    graph.add_edge("strategy_review", "generate_batch")
    graph.add_edge("filter_editable_items", "revise_items")
    graph.add_edge("generate_batch", "quality_feedback")
    graph.add_edge("revise_items", "quality_feedback")
    graph.add_edge("quality_feedback", END)
    graph.add_edge("export_csv", END)
    return graph.compile()


escouade_graph = build_escouade_graph()
