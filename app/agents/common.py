import json
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.core.config import get_settings
from app.db.schema import ensure_n8n_chat_histories_metadata
from app.db.session import db_connection
from app.services.agents import get_agent_histories, get_agent_session_id
from app.services.connections import find_connection_for_context
from app.services.web_crawler import CrawlerConfig, crawl_website, validate_public_url

URL_PATTERN = re.compile(r"https?://[^\s<>()\"']+", re.IGNORECASE)
BARE_DOMAIN_PATTERN = re.compile(
    r"(?<![@\w])(?:www\.)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s<>()\"']*)?",
    re.IGNORECASE,
)
PLATFORM_KNOWLEDGE_PATH = Path(__file__).resolve().parent / "prompts" / "b10x_social_planner_knowledge.md"


def read_prompt(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def read_platform_knowledge() -> str:
    return PLATFORM_KNOWLEDGE_PATH.read_text(encoding="utf-8")


def build_chat_llm(model: str):
    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not configured.")
    return ChatOpenAI(
        model=model,
        api_key=settings.openai_api_key,
        temperature=0.6,
        timeout=settings.agent_timeout_seconds,
    )


def validate_agent_context(session: dict[str, Any], agent_id: str, provided_session_id: str | None = None) -> tuple[str, list[dict[str, Any]]]:
    if not find_connection_for_context(session):
        raise HTTPException(status_code=403, detail="This app session is not linked to an installed account.")

    session_id = get_agent_session_id(session, agent_id, provided_session_id)
    history = get_agent_histories(session, [agent_id], validate_install=False)[0]["messages"]
    return session_id, history


def extract_urls(text: str, limit: int = 8) -> list[str]:
    urls = []
    seen = set()
    source = text or ""
    full_url_spans = []
    candidates = []

    for match in URL_PATTERN.finditer(source):
        full_url_spans.append(match.span())
        candidates.append((match.group(0), False))

    for match in BARE_DOMAIN_PATTERN.finditer(source):
        span = match.span()
        if any(span[0] >= full_span[0] and span[1] <= full_span[1] for full_span in full_url_spans):
            continue
        candidates.append((f"https://{match.group(0)}", True))

    validated = []
    full_url_hosts_with_paths = set()
    for candidate, is_bare_domain in candidates:
        raw_url = candidate.rstrip(".,;:!?)]}")
        try:
            url = validate_public_url(raw_url)
        except ValueError:
            continue
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower().removeprefix("www.")
        if not is_bare_domain and parsed.path not in {"", "/"}:
            full_url_hosts_with_paths.add(hostname)
        validated.append((url, is_bare_domain))

    for url, is_bare_domain in validated:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower().removeprefix("www.")
        if is_bare_domain and parsed.path in {"", "/"} and hostname in full_url_hosts_with_paths:
            continue
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= limit:
            break
    return urls


def crawl_urls_to_context(urls: list[str], max_total_pages: int = 10) -> str:
    if not urls:
        return ""

    chunks = []
    remaining_pages = max_total_pages
    for url in urls:
        if remaining_pages <= 0:
            break

        is_multi_url_request = len(urls) > 1
        per_url_pages = 1 if is_multi_url_request else min(5, remaining_pages)
        try:
            result = crawl_website(
                url,
                CrawlerConfig(
                    max_pages=per_url_pages,
                    max_depth=0 if is_multi_url_request else 1,
                    max_total_chars=45000,
                    max_chars_per_page=12000,
                ),
            )
            if result.content:
                chunks.append(result.content)
            remaining_pages -= max(1, len(result.pages))
        except Exception as exc:
            chunks.append(f"=== PAGE: {url} ===\nUnable to retrieve page context: {exc}")
            remaining_pages -= 1

    return "\n\n".join(chunks).strip()


def history_to_messages(history: list[dict[str, Any]], max_messages: int = 30) -> list:
    messages = []
    for message in history[-max_messages:]:
        role = message.get("role")
        content = message.get("content") or ""
        if not content:
            continue
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))
    return messages


def build_messages(system_parts: list[str], history: list[dict[str, Any]], user_message: str) -> list:
    messages = [SystemMessage(content="\n\n".join(part for part in system_parts if part).strip())]
    messages.extend(history_to_messages(history))
    messages.append(HumanMessage(content=user_message))
    return messages


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
