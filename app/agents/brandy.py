from pathlib import Path
from typing import Any, TypedDict

from fastapi import HTTPException
from langgraph.graph import END, StateGraph

from app.agents.common import (
    build_chat_llm,
    build_messages,
    crawl_urls_to_context,
    extract_urls,
    read_platform_knowledge,
    read_prompt,
    save_message,
    validate_agent_context,
)
from app.core.config import get_settings
from app.db.session import db_connection
from app.services.agent_context import build_upstream_context, format_upstream_context
from app.services.output_processor import strip_markers_from_payload
from app.services.token_usage import record_token_usage

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "brandy.md"


class BrandyState(TypedDict, total=False):
    session: dict[str, Any]
    session_id: str
    user_message: str
    history: list[dict[str, Any]]
    urls: list[str]
    url_context: str
    upstream_context: dict[str, Any]
    upstream_context_text: str
    reply: str


def load_context(state: BrandyState) -> BrandyState:
    session_id, history = validate_agent_context(
        state["session"],
        "brandy",
        state.get("session_id"),
    )
    state["session_id"] = session_id
    state["history"] = history
    return state


def load_upstream_context(state: BrandyState) -> BrandyState:
    location_id = state["session"].get("activeLocation") or state["session"].get("locationId")
    with db_connection() as conn:
        cursor = conn.cursor()
        try:
            upstream_context = build_upstream_context(cursor, location_id, "brandy")
        finally:
            cursor.close()

    state["upstream_context"] = upstream_context
    state["upstream_context_text"] = format_upstream_context(upstream_context)
    return state


def inspect_urls(state: BrandyState) -> BrandyState:
    state["urls"] = extract_urls(state.get("user_message", ""))
    return state


def crawl_urls(state: BrandyState) -> BrandyState:
    state["url_context"] = crawl_urls_to_context(state.get("urls") or [])
    return state


def system_parts(state: BrandyState) -> list[str]:
    parts = [read_prompt(PROMPT_PATH), read_platform_knowledge()]

    if state.get("upstream_context_text"):
        parts.append(
            "Upstream Ascala context is provided below. Use this as strategic context. "
            "For Brandy, Molly context should guide audience alignment, not replace brand voice evidence.\n\n"
            f"{state['upstream_context_text']}"
        )

    if state.get("url_context"):
        parts.append(
            "Retrieved URL context is provided below. Use it as source material for brand-owned evidence. "
            "Do not claim to know anything beyond this retrieved context.\n\n"
            f"{state['url_context']}"
        )

    return parts


def call_model(state: BrandyState) -> BrandyState:
    settings = get_settings()
    response = build_chat_llm(settings.brandy_model).invoke(
        build_messages(system_parts(state), state.get("history", []), state["user_message"])
    )
    record_token_usage(
        session=state["session"],
        agent_id="brandy",
        model=settings.brandy_model,
        response=response,
    )
    state["reply"] = response.content if isinstance(response.content, str) else str(response.content)
    return state


def save_chat(state: BrandyState) -> BrandyState:
    session = state["session"]
    session_id = state["session_id"]
    save_message(session, session_id, "brandy", "user", state["user_message"])
    save_message(session, session_id, "brandy", "assistant", state["reply"])
    return state


def build_brandy_graph():
    graph = StateGraph(BrandyState)
    graph.add_node("load_context", load_context)
    graph.add_node("load_upstream_context", load_upstream_context)
    graph.add_node("inspect_urls", inspect_urls)
    graph.add_node("crawl_urls", crawl_urls)
    graph.add_node("call_model", call_model)
    graph.add_node("save_chat", save_chat)

    graph.set_entry_point("load_context")
    graph.add_edge("load_context", "load_upstream_context")
    graph.add_edge("load_upstream_context", "inspect_urls")
    graph.add_edge("inspect_urls", "crawl_urls")
    graph.add_edge("crawl_urls", "call_model")
    graph.add_edge("call_model", "save_chat")
    graph.add_edge("save_chat", END)
    return graph.compile()


brandy_graph = build_brandy_graph()


def run_brandy_chat(session: dict, message: str, session_id: str | None = None) -> dict:
    if not message:
        raise HTTPException(status_code=400, detail="message is required.")

    state = brandy_graph.invoke({
        "session": session,
        "session_id": session_id,
        "user_message": message,
    })
    raw_reply = state.get("reply", "")

    return {
        "payload": strip_markers_from_payload(raw_reply),
        "rawPayload": raw_reply,
        "sessionId": state["session_id"],
    }
