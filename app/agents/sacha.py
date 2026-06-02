from pathlib import Path
from typing import Any, TypedDict

from fastapi import HTTPException
from langgraph.graph import END, StateGraph

from app.agents.common import (
    build_chat_llm,
    build_messages,
    crawl_urls_to_context,
    extract_urls,
    read_prompt,
    save_message,
    validate_agent_context,
)
from app.core.config import get_settings
from app.db.session import db_connection
from app.services.agent_context import build_upstream_context, format_upstream_context
from app.services.output_processor import strip_markers_from_payload

PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "sacha.md"


class SachaState(TypedDict, total=False):
    session: dict[str, Any]
    session_id: str
    user_message: str
    history: list[dict[str, Any]]
    urls: list[str]
    url_context: str
    upstream_context: dict[str, Any]
    upstream_context_text: str
    reply: str


def load_context(state: SachaState) -> SachaState:
    session_id, history = validate_agent_context(
        state["session"],
        "sacha",
        state.get("session_id"),
    )
    state["session_id"] = session_id
    state["history"] = history
    return state


def load_upstream_context(state: SachaState) -> SachaState:
    location_id = state["session"].get("activeLocation") or state["session"].get("locationId")
    with db_connection() as conn:
        cursor = conn.cursor()
        try:
            upstream_context = build_upstream_context(cursor, location_id, "sacha")
        finally:
            cursor.close()

    state["upstream_context"] = upstream_context
    state["upstream_context_text"] = format_upstream_context(upstream_context)
    return state


def inspect_urls(state: SachaState) -> SachaState:
    state["urls"] = extract_urls(state.get("user_message", ""))
    return state


def crawl_urls(state: SachaState) -> SachaState:
    state["url_context"] = crawl_urls_to_context(state.get("urls") or [])
    return state


def system_parts(state: SachaState) -> list[str]:
    parts = [read_prompt(PROMPT_PATH)]

    if state.get("upstream_context_text"):
        parts.append(
            "Upstream Ascala context is provided below. Use Molly for audience, positioning, "
            "buyer psychology, and content direction. Use Brandy for voice, tone, language, "
            "CTA rules, and guardrails. Do not ask the user for details already clearly present here.\n\n"
            f"{state['upstream_context_text']}"
        )

    if state.get("url_context"):
        parts.append(
            "Retrieved URL context is provided below. Use it as source material for the user's "
            "current content, offers, website, or social presence. Do not claim to know anything "
            "beyond this retrieved context.\n\n"
            f"{state['url_context']}"
        )

    return parts


def call_model(state: SachaState) -> SachaState:
    settings = get_settings()
    response = build_chat_llm(settings.sacha_model).invoke(
        build_messages(system_parts(state), state.get("history", []), state["user_message"])
    )
    state["reply"] = response.content if isinstance(response.content, str) else str(response.content)
    return state


def save_chat(state: SachaState) -> SachaState:
    session = state["session"]
    session_id = state["session_id"]
    save_message(session, session_id, "sacha", "user", state["user_message"])
    save_message(session, session_id, "sacha", "assistant", state["reply"])
    return state


def build_sacha_graph():
    graph = StateGraph(SachaState)
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


sacha_graph = build_sacha_graph()


def run_sacha_chat(session: dict, message: str, session_id: str | None = None) -> dict:
    if not message:
        raise HTTPException(status_code=400, detail="message is required.")

    state = sacha_graph.invoke({
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
