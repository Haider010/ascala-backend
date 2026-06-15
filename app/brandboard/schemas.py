from typing import Literal

from pydantic import BaseModel, Field


BrandBoardTheme = Literal["premium_dark", "clean_light", "soft_lifestyle", "bold_editorial"]


class CoverSystem(BaseModel):
    document_title: str = Field(..., description="A short premium title, usually 2-5 words.")
    eyebrow: str = Field(..., description="Small contextual label for the cover.")
    subtitle: str = Field(..., description="One concise sentence explaining the brand guideline.")
    version_label: str = Field(..., description="Version or internal-use style label.")


class BrandFoundation(BaseModel):
    mission: str
    positioning: str
    brand_promise: str
    personality: list[str]
    transformation: str
    strategic_principles: list[str]


class ColorToken(BaseModel):
    name: str
    hex: str = Field(..., description="A valid hex color value.")
    role: str
    usage_note: str


class GradientToken(BaseModel):
    name: str
    css_value: str
    role: str


class TextColorSystem(BaseModel):
    primary: ColorToken
    body: ColorToken
    muted: ColorToken


class ColorSystem(BaseModel):
    palette: list[ColorToken] = Field(..., min_length=6, max_length=10)
    text_colors: TextColorSystem
    gradients: list[GradientToken] = Field(default_factory=list, max_length=3)
    rules_do: list[str]
    rules_never: list[str]


class TypefaceSpec(BaseModel):
    name: str
    role: str
    rationale: str
    weights: str


class TypeScaleItem(BaseModel):
    label: str
    example: str
    font_size: str
    weight: str
    line_height: str
    letter_spacing: str
    usage: str


class TypographySystem(BaseModel):
    primary_typeface: TypefaceSpec
    accent_typeface: TypefaceSpec
    type_scale: list[TypeScaleItem] = Field(..., min_length=5, max_length=8)
    rules_do: list[str]
    rules_never: list[str]


class ButtonStyle(BaseModel):
    name: str
    role: str
    label_examples: list[str]
    background: str
    text_color: str
    border: str
    radius: str
    usage_note: str


class ButtonSystem(BaseModel):
    styles: list[ButtonStyle] = Field(..., min_length=3, max_length=6)
    cta_language: list[str]
    rules_do: list[str]
    rules_never: list[str]


class SpacingToken(BaseModel):
    token: str
    value: str
    usage: str


class RadiusToken(BaseModel):
    token: str
    value: str
    usage: str


class SpacingRadiusSystem(BaseModel):
    spacing_scale: list[SpacingToken] = Field(..., min_length=5, max_length=8)
    radius_scale: list[RadiusToken] = Field(..., min_length=3, max_length=5)
    layout_rules: list[str]


class ComponentPattern(BaseModel):
    name: str
    purpose: str
    structure: str
    style_notes: list[str]
    avoid: list[str]


class UiComponentSystem(BaseModel):
    patterns: list[ComponentPattern] = Field(..., min_length=4, max_length=8)
    surface_rules: list[str]
    interaction_rules: list[str]


class ImagerySystem(BaseModel):
    photography_direction: str
    environment_direction: str
    composition_direction: str
    visual_metaphors: list[str]
    image_do: list[str]
    image_never: list[str]


class VoiceToneSystem(BaseModel):
    voice_traits: list[str] = Field(..., min_length=3, max_length=6)
    tone_description: str
    write_this: list[str]
    not_this: list[str]
    signature_phrases: list[str]
    copy_rules: list[str]


class AudienceMessagingSystem(BaseModel):
    primary_audience: str
    audience_desires: list[str]
    audience_pains: list[str]
    buying_triggers: list[str]
    objection_language: list[str]
    message_pillars: list[str]
    hero_message_examples: list[str]


class ContentApplicationSystem(BaseModel):
    content_pillars: list[str]
    social_post_rules: list[str]
    landing_page_rules: list[str]
    email_rules: list[str]
    cta_examples: list[str]


class ThemeSelection(BaseModel):
    theme: BrandBoardTheme
    reason: str


class BrandGuidelinesDocument(BaseModel):
    cover: CoverSystem
    brand_foundation: BrandFoundation
    audience_messaging: AudienceMessagingSystem
    color_system: ColorSystem
    typography_system: TypographySystem
    button_system: ButtonSystem
    spacing_radius_system: SpacingRadiusSystem
    ui_component_system: UiComponentSystem
    imagery_system: ImagerySystem
    voice_tone_system: VoiceToneSystem
    content_application_system: ContentApplicationSystem
    theme_selection: ThemeSelection


BrandBoardDocument = BrandGuidelinesDocument


class BrandBoardResponse(BaseModel):
    output: BrandBoardDocument | None = None
    workflowStatus: dict | None = None
    updatedAt: str | None = None
    generated: bool = False
