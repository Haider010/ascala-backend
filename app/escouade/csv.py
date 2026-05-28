import csv
import io
from typing import Any

CSV_COLUMNS = {
    "carrousel": [
        "post_id", "platform", "objective", "cover_headline", "caption", "cta", "hashtags",
        "slide_1_headline", "slide_1_body", "slide_1_visual_direction",
        "slide_2_headline", "slide_2_body", "slide_2_visual_direction",
        "slide_3_headline", "slide_3_body", "slide_3_visual_direction",
        "slide_4_headline", "slide_4_body", "slide_4_visual_direction",
        "slide_5_headline", "slide_5_body", "slide_5_visual_direction",
        "slide_6_headline", "slide_6_body", "slide_6_visual_direction",
        "slide_7_headline", "slide_7_body", "slide_7_visual_direction",
        "slide_8_headline", "slide_8_body", "slide_8_visual_direction",
        "slide_9_headline", "slide_9_body", "slide_9_visual_direction",
        "slide_10_headline", "slide_10_body", "slide_10_visual_direction",
    ],
    "reel": ["post_id", "platform", "objective", "hook", "script", "b_roll_direction", "on_screen_text", "caption", "cta", "hashtags"],
    "image_post": ["post_id", "platform", "objective", "overlay_text", "visual_direction", "caption", "cta", "hashtags"],
    "stories": [
        "post_id", "platform", "objective", "sequence_name", "final_cta", "cta",
        "slide_1_text", "slide_1_interaction", "slide_1_visual_direction",
        "slide_2_text", "slide_2_interaction", "slide_2_visual_direction",
        "slide_3_text", "slide_3_interaction", "slide_3_visual_direction",
        "slide_4_text", "slide_4_interaction", "slide_4_visual_direction",
        "slide_5_text", "slide_5_interaction", "slide_5_visual_direction",
    ],
    "text_post": ["post_id", "platform", "objective", "hook", "body", "closing_line", "cta", "hashtags"],
}


def flatten_item(member_type: str, item: dict) -> dict:
    flattened = dict(item)

    if member_type == "carrousel":
        for slide in item.get("slides") or []:
            number = slide.get("slide_number")
            if not number:
                continue
            flattened[f"slide_{number}_headline"] = slide.get("headline")
            flattened[f"slide_{number}_body"] = slide.get("body")
            flattened[f"slide_{number}_visual_direction"] = slide.get("visual_direction")

    if member_type == "stories":
        for slide in item.get("slides") or []:
            number = slide.get("slide_number")
            if not number:
                continue
            flattened[f"slide_{number}_text"] = slide.get("text")
            flattened[f"slide_{number}_interaction"] = slide.get("interaction")
            flattened[f"slide_{number}_visual_direction"] = slide.get("visual_direction")

    return flattened


def csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(str(item) if not isinstance(item, dict) else "; ".join(f"{k}: {v}" for k, v in item.items()) for item in value)
    if isinstance(value, dict):
        return "; ".join(f"{key}: {item}" for key, item in value.items())
    return str(value)


def build_items_csv(member_type: str, items: list[dict]) -> str:
    columns = CSV_COLUMNS.get(member_type, ["post_id", "platform", "objective", "content"])
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()

    for item in items:
        flattened = flatten_item(member_type, item)
        row = {column: csv_value(flattened.get(column)) for column in columns}
        writer.writerow(row)

    return buffer.getvalue()
