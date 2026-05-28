from typing import Literal

from pydantic import BaseModel, Field


class BaseContentItem(BaseModel):
    post_id: str = Field(..., description="Stable short ID such as CAR-001, REEL-001, IMG-001.")
    platform: str
    objective: str
    content_style: str
    cta: str | None = None
    strategic_note: str | None = None


class CarouselSlide(BaseModel):
    slide_number: int
    role: str
    headline: str
    body: str
    visual_direction: str | None = None


class CarouselItem(BaseContentItem):
    member_type: Literal["carrousel"] = "carrousel"
    cover_headline: str
    slides: list[CarouselSlide] = Field(..., min_length=2)
    caption: str
    hashtags: list[str] = Field(default_factory=list)


class ReelItem(BaseContentItem):
    member_type: Literal["reel"] = "reel"
    hook: str
    script: str
    b_roll_direction: str | None = None
    on_screen_text: list[str] = Field(default_factory=list)
    caption: str
    hashtags: list[str] = Field(default_factory=list)


class ImagePostItem(BaseContentItem):
    member_type: Literal["image_post"] = "image_post"
    overlay_text: str
    visual_direction: str
    caption: str
    hashtags: list[str] = Field(default_factory=list)


class StorySlide(BaseModel):
    slide_number: int
    purpose: str
    text: str
    interaction: str | None = None
    visual_direction: str | None = None


class StoriesItem(BaseContentItem):
    member_type: Literal["stories"] = "stories"
    sequence_name: str
    slides: list[StorySlide] = Field(..., min_length=2)
    final_cta: str | None = None


class TextPostItem(BaseContentItem):
    member_type: Literal["text_post"] = "text_post"
    hook: str
    body: str
    closing_line: str
    hashtags: list[str] = Field(default_factory=list)


class CarouselBatchOutput(BaseModel):
    items: list[CarouselItem]
    quality_note: str | None = None


class ReelBatchOutput(BaseModel):
    items: list[ReelItem]
    quality_note: str | None = None


class ImagePostBatchOutput(BaseModel):
    items: list[ImagePostItem]
    quality_note: str | None = None


class StoriesBatchOutput(BaseModel):
    items: list[StoriesItem]
    quality_note: str | None = None


class TextPostBatchOutput(BaseModel):
    items: list[TextPostItem]
    quality_note: str | None = None


MEMBER_OUTPUT_SCHEMAS = {
    "carrousel": CarouselBatchOutput,
    "reel": ReelBatchOutput,
    "image_post": ImagePostBatchOutput,
    "stories": StoriesBatchOutput,
    "text_post": TextPostBatchOutput,
}

MEMBER_ITEM_SCHEMAS = {
    "carrousel": CarouselItem,
    "reel": ReelItem,
    "image_post": ImagePostItem,
    "stories": StoriesItem,
    "text_post": TextPostItem,
}
