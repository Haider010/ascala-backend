import json
import re
from typing import Any


MEMBER_TYPE_ALIASES = {
    "carousel": "carrousel",
    "carrousel": "carrousel",
    "carousels": "carrousel",
    "carrousels": "carrousel",
    "reel": "reel",
    "reels": "reel",
    "image": "image_post",
    "image_post": "image_post",
    "image post": "image_post",
    "image posts": "image_post",
    "static image": "image_post",
    "static images": "image_post",
    "stories": "stories",
    "story": "stories",
    "text": "text_post",
    "text_post": "text_post",
    "text post": "text_post",
    "text posts": "text_post",
    "long form": "text_post",
}

SOURCE_TYPE_OPTIONS = [
    "Sacha Series",
    "Sacha Theme",
    "Sacha Weekly Plan",
    "Sacha Content Idea",
    "Custom Topic",
    "Selected Reference",
]
PLATFORM_OPTIONS = ["Instagram", "Facebook", "LinkedIn", "TikTok", "YouTube Shorts", "Multi-platform"]
OBJECTIVE_OPTIONS = [
    "Visibility",
    "Engagement",
    "Follower growth",
    "Lead Generation",
    "Sales",
    "Authority building",
    "Community growth",
    "Appointment bookings",
    "Webinar registrations",
]
LANGUAGE_OPTIONS = ["English", "French Québec", "Bilingual", "Use brand default"]
CONTENT_STYLE_OPTIONS = [
    "Educational",
    "Premium",
    "Friendly",
    "Bold",
    "Playful",
    "Thought leadership",
    "Soft-sell",
    "Direct response",
    "Practical/how-to",
    "Myth-busting",
    "Behind-the-scenes",
    "Story-driven",
    "Conversion-focused",
]
CTA_OPTIONS = ["No CTA", "Soft CTA", "Engagement CTA", "Lead generation CTA", "Sales CTA", "Custom CTA"]
INTERACTION_OPTIONS = [
    "Fast & Efficient",
    "Creative Partner",
    "Strategic Coach",
    "Social Media Manager Mode",
    "Friendly Best Buddy",
    "Premium Brand Editor",
    "Direct Response Copywriter",
]
QUANTITY_OPTIONS = [5, 10, 15, 20, 30]

SECTION_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
JSON_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.IGNORECASE)


def _normalize_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").strip().lower()).strip()


def _first_value(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _string_or_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value).strip() or None


def _as_list(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _normalize_choice(value: Any, options: list[str]) -> str | None:
    if value in (None, ""):
        return None

    normalized = _normalize_text(value)
    if not normalized:
        return None

    for option in options:
        if _normalize_text(option) == normalized:
            return option

    for option in options:
        option_key = _normalize_text(option)
        if normalized in option_key or option_key in normalized:
            return option

    return _string_or_none(value)


def _normalize_member_type(value: Any) -> str | None:
    normalized = _normalize_text(value).replace(" ", "_")
    if normalized in MEMBER_TYPE_ALIASES:
        return MEMBER_TYPE_ALIASES[normalized]

    spaced = normalized.replace("_", " ")
    if spaced in MEMBER_TYPE_ALIASES:
        return MEMBER_TYPE_ALIASES[spaced]

    for alias, member_type in MEMBER_TYPE_ALIASES.items():
        alias_key = alias.replace("_", " ")
        if alias_key and alias_key in spaced:
            return member_type

    return None


def _normalize_quantity(value: Any) -> int | None:
    if value in (None, ""):
        return None

    match = re.search(r"\d+", str(value))
    if not match:
        return None

    quantity = int(match.group(0))
    if quantity in QUANTITY_OPTIONS:
        return quantity

    return min(QUANTITY_OPTIONS, key=lambda option: abs(option - quantity))


def _extract_section(markdown: str) -> str:
    matches = list(SECTION_PATTERN.finditer(markdown or ""))
    for index, match in enumerate(matches):
        heading = _normalize_text(match.group(2))
        if "escouade" not in heading or "brief" not in heading:
            continue

        start = match.end()
        current_level = len(match.group(1))
        end = len(markdown)
        for next_match in matches[index + 1 :]:
            if len(next_match.group(1)) <= current_level:
                end = next_match.start()
                break
        return markdown[start:end].strip()

    return markdown or ""


def _extract_json(section: str) -> dict[str, Any] | None:
    candidates = [match.group(1) for match in JSON_BLOCK_PATTERN.finditer(section or "")]
    stripped = (section or "").strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        candidates.append(stripped)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    return None


def _parse_key_value_lines(section: str) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for raw_line in (section or "").splitlines():
        line = raw_line.strip().lstrip("-*").strip()
        if ":" not in line:
            continue

        key, value = line.split(":", 1)
        key = re.sub(r"[^a-z0-9]+", "_", key.strip().lower()).strip("_")
        parsed[key] = value.strip()
    return parsed


def normalize_escouade_brief(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not raw:
        return None

    raw_filters = raw.get("filters") if isinstance(raw.get("filters"), dict) else {}
    member_type = _normalize_member_type(_first_value(raw.get("member_type"), raw.get("format"), raw.get("content_type")))
    source_type = _normalize_choice(_first_value(raw.get("source_type"), raw_filters.get("source_type")), SOURCE_TYPE_OPTIONS)
    source_label = _string_or_none(_first_value(raw.get("source_label"), raw.get("source"), raw.get("topic"), raw_filters.get("source_label")))
    primary_platform = _normalize_choice(
        _first_value(raw.get("primary_platform"), raw.get("platform"), raw_filters.get("primary_platform")),
        PLATFORM_OPTIONS,
    )
    platforms = [
        normalized
        for normalized in (
            _normalize_choice(platform, PLATFORM_OPTIONS)
            for platform in _as_list(_first_value(raw.get("platforms"), raw_filters.get("platforms"), primary_platform))
        )
        if normalized
    ]
    if primary_platform and primary_platform not in platforms:
        platforms.insert(0, primary_platform)
    if not primary_platform and platforms:
        primary_platform = platforms[0]

    content_style = [
        normalized
        for normalized in (
            _normalize_choice(style, CONTENT_STYLE_OPTIONS)
            for style in _as_list(_first_value(raw.get("content_style"), raw_filters.get("content_style")))
        )
        if normalized
    ]

    filters = {
        "source_type": source_type,
        "source_label": source_label,
        "platforms": platforms,
        "primary_platform": primary_platform,
        "objective": _normalize_choice(_first_value(raw.get("objective"), raw_filters.get("objective")), OBJECTIVE_OPTIONS),
        "content_style": content_style,
        "quantity": _normalize_quantity(_first_value(raw.get("quantity"), raw_filters.get("quantity"))),
        "cta_preference": _normalize_choice(_first_value(raw.get("cta_preference"), raw.get("cta"), raw_filters.get("cta_preference")), CTA_OPTIONS),
        "language": _normalize_choice(_first_value(raw.get("language"), raw_filters.get("language")), LANGUAGE_OPTIONS),
        "interaction_style": _normalize_choice(
            _first_value(raw.get("interaction_style"), raw_filters.get("interaction_style")),
            INTERACTION_OPTIONS,
        ),
        "special_instructions": _string_or_none(
            _first_value(raw.get("special_instructions"), raw.get("production_notes"), raw.get("message"), raw_filters.get("special_instructions"))
        ),
        "format_filters": raw.get("format_filters") if isinstance(raw.get("format_filters"), dict) else raw_filters.get("format_filters", {}),
    }
    filters = {key: value for key, value in filters.items() if value not in (None, "", [], {})}

    normalized = {
        "batch_name": _string_or_none(raw.get("batch_name")),
        "member_type": member_type,
        "source_type": source_type,
        "source_label": source_label,
        "filters": filters,
        "message": _string_or_none(_first_value(raw.get("message"), raw.get("production_notes"), filters.get("special_instructions"))),
    }
    return {key: value for key, value in normalized.items() if value not in (None, "", [], {})}


def extract_escouade_brief(markdown: str) -> dict[str, Any] | None:
    section = _extract_section(markdown)
    raw = _extract_json(section) or _parse_key_value_lines(section)
    return normalize_escouade_brief(raw)
