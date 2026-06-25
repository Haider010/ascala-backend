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
            flattened[f"slide_{number}_role"] = slide.get("role")
            flattened[f"slide_{number}_headline"] = slide.get("headline")
            flattened[f"slide_{number}_body"] = slide.get("body")
            flattened[f"slide_{number}_visual_direction"] = slide.get("visual_direction")

    if member_type == "reel":
        on_screen_text = item.get("on_screen_text") or []
        flattened["on_screen_text"] = "\n".join(str(beat) for beat in on_screen_text)
        for number, beat in enumerate(on_screen_text, start=1):
            flattened[f"field_{number}"] = beat

    if member_type == "image_post":
        flattened["headline"] = item.get("overlay_text")
        flattened["supporting_line"] = item.get("strategic_note")

    if member_type == "stories":
        for slide in item.get("slides") or []:
            number = slide.get("slide_number")
            if not number:
                continue
            flattened[f"slide_{number}_purpose"] = slide.get("purpose")
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


def build_production_table_csv(columns: list[dict], rows: list[dict]) -> str:
    normalized_columns = [
        column for column in columns
        if isinstance(column, dict) and column.get("key")
    ]
    column_pairs = []
    seen_labels: dict[str, int] = {}
    for column in normalized_columns:
        key = str(column["key"])
        label = str(column.get("label") or key)
        if label in seen_labels:
            seen_labels[label] += 1
            label = f"{label} {seen_labels[label]}"
        else:
            seen_labels[label] = 1
        column_pairs.append((key, label))

    if not column_pairs:
        column_pairs = [(key, key) for key in sorted({key for row in rows for key in row.keys()})]

    buffer = io.StringIO()
    fieldnames = [label for _key, label in column_pairs]
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()

    for row in rows:
        writer.writerow({label: csv_value(row.get(key)) for key, label in column_pairs})

    return buffer.getvalue()
