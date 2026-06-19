import json
import re
from pathlib import Path
from typing import Any, Literal, TypedDict

from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

from app.core.config import get_settings
from app.agents.common import read_platform_knowledge
from app.db.schema import ensure_n8n_chat_histories_metadata
from app.db.session import db_connection
from app.services.agents import get_agent_histories, get_agent_session_id
from app.services.connections import find_connection_for_context
from app.services.output_processor import strip_ascala_markers, strip_markers_from_payload
from app.services.token_usage import record_token_usage
from app.services.web_crawler import CrawlerConfig, crawl_website, validate_public_url

URL_PATTERN = re.compile(r"https?://[^\s<>()\"']+", re.IGNORECASE)
BARE_DOMAIN_PATTERN = re.compile(
    r"(?<![@\w])(?:www\.)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s<>()\"']*)?",
    re.IGNORECASE,
)
PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "molly.md"


class MollyState(TypedDict, total=False):
    session: dict[str, Any]
    session_id: str
    user_message: str
    history: list[dict[str, Any]]
    urls: list[str]
    url_context: str
    reply: str


def extract_urls(text: str) -> list[str]:
    urls = []
    seen = set()
    source = text or ""
    full_url_spans = []
    candidates = []

    for match in URL_PATTERN.finditer(source):
        full_url_spans.append(match.span())
        candidates.append(match.group(0))

    for match in BARE_DOMAIN_PATTERN.finditer(source):
        span = match.span()
        if any(span[0] >= full_span[0] and span[1] <= full_span[1] for full_span in full_url_spans):
            continue
        candidates.append(f"https://{match.group(0)}")

    for candidate in candidates:
        raw_url = candidate.rstrip(".,;:!?)]}")
        try:
            url = validate_public_url(raw_url)
        except ValueError:
            continue
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def read_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def build_llm():
    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not configured.")
    return ChatOpenAI(model=settings.molly_model, api_key=settings.openai_api_key, temperature=0.6)


def db_message(role: Literal["user", "assistant"], content: str) -> dict[str, Any]:
    return {
        "type": "human" if role == "user" else "ai",
        "role": role,
        "content": content,
    }


def save_message(session: dict[str, Any], session_id: str, agent_id: str, role: Literal["user", "assistant"], content: str) -> None:
    with db_connection() as conn:
        cursor = conn.cursor()
        try:
            ensure_n8n_chat_histories_metadata(cursor)
            cursor.execute(
                """
                INSERT INTO n8n_chat_histories (
                    session_id,
                    message,
                    location_id,
                    agent_id,
                    user_id
                )
                VALUES (%s, %s::jsonb, %s, %s, %s)
                """,
                (
                    session_id,
                    json.dumps(db_message(role, content)),
                    session.get("activeLocation"),
                    agent_id,
                    session.get("userId"),
                ),
            )
            conn.commit()
        finally:
            cursor.close()


def load_context(state: MollyState) -> MollyState:
    session = state["session"]
    if not find_connection_for_context(session):
        raise HTTPException(status_code=403, detail="This app session is not linked to an installed account.")

    session_id = get_agent_session_id(session, "molly", state.get("session_id"))
    state["session_id"] = session_id
    history = get_agent_histories(session, ["molly"], validate_install=False)[0]["messages"]
    state["history"] = history
    return state


def inspect_urls(state: MollyState) -> MollyState:
    state["urls"] = extract_urls(state.get("user_message", ""))[:3]
    return state


def crawl_urls(state: MollyState) -> MollyState:
    urls = state.get("urls") or []
    if not urls:
        state["url_context"] = ""
        return state

    chunks = []
    remaining_pages = 10
    for url in urls:
        if remaining_pages <= 0:
            break
        per_url_pages = min(5, remaining_pages)
        try:
            result = crawl_website(
                url,
                CrawlerConfig(
                    max_pages=per_url_pages,
                    max_depth=1,
                    max_total_chars=45000,
                    max_chars_per_page=12000,
                ),
            )
            if result.content:
                chunks.append(result.content)
            remaining_pages -= len(result.pages)
        except Exception as exc:
            chunks.append(f"=== PAGE: {url} ===\nUnable to retrieve page context: {exc}")
            remaining_pages -= 1

    state["url_context"] = "\n\n".join(chunks).strip()
    return state


def build_messages(state: MollyState) -> list:
    system_parts = [read_prompt(), read_platform_knowledge()]
    if state.get("url_context"):
        system_parts.append(
            "Retrieved URL context is provided below. Use it as source material. "
            "Do not claim to know anything beyond this retrieved context.\n\n"
            f"{state['url_context']}"
        )

    messages = [SystemMessage(content="\n\n".join(system_parts))]
    for message in state.get("history", [])[-30:]:
        role = message.get("role")
        content = message.get("content") or ""
        if not content:
            continue
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))

    messages.append(HumanMessage(content=state["user_message"]))
    return messages


def call_model(state: MollyState) -> MollyState:
    settings = get_settings()
    response = build_llm().invoke(build_messages(state))
    record_token_usage(
        session=state["session"],
        agent_id="molly",
        model=settings.molly_model,
        response=response,
    )
    content = response.content if isinstance(response.content, str) else str(response.content)
    state["reply"] = content
    return state


def save_chat(state: MollyState) -> MollyState:
    session = state["session"]
    session_id = state["session_id"]
    save_message(session, session_id, "molly", "user", state["user_message"])
    save_message(session, session_id, "molly", "assistant", state["reply"])
    return state


def build_molly_graph():
    graph = StateGraph(MollyState)
    graph.add_node("load_context", load_context)
    graph.add_node("inspect_urls", inspect_urls)
    graph.add_node("crawl_urls", crawl_urls)
    graph.add_node("call_model", call_model)
    graph.add_node("save_chat", save_chat)

    graph.set_entry_point("load_context")
    graph.add_edge("load_context", "inspect_urls")
    graph.add_edge("inspect_urls", "crawl_urls")
    graph.add_edge("crawl_urls", "call_model")
    graph.add_edge("call_model", "save_chat")
    graph.add_edge("save_chat", END)
    return graph.compile()


molly_graph = build_molly_graph()


def run_molly_chat(session: dict, message: str, session_id: str | None = None) -> dict:
    if not message:
        raise HTTPException(status_code=400, detail="message is required.")

    state = molly_graph.invoke({
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
