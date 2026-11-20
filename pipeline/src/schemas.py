from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

FLAVOR_CATEGORIES: tuple[str, ...] = (
    "Fruity",
    "Floral",
    "Sweet & Sugary",
    "Nutty & Cocoa",
    "Spices",
    "Roasted & Toasty",
    "Green & Vegetative",
    "Fermented & Boozy",
)

FlavorCategory = Literal[
    "Fruity",
    "Floral",
    "Sweet & Sugary",
    "Nutty & Cocoa",
    "Spices",
    "Roasted & Toasty",
    "Green & Vegetative",
    "Fermented & Boozy",
]

ExtractionConfidence = Literal["high", "medium", "low"]
RoasterPlatform = Literal["shopify", "woocommerce", "manual"]


class RoasterConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    city: str | None = None
    website_url: str | None = None
    platform: RoasterPlatform
    collection_handle: str = "all"
    exclude_keywords: list[str] = Field(default_factory=list)

    def to_db_row(self) -> dict[str, str | None]:
        return {
            "id": self.id,
            "name": self.name,
            "city": self.city,
            "website_url": self.website_url,
            "platform": self.platform,
        }


class EstateMasterRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(..., min_length=1)
    aliases: list[str] = Field(default_factory=list)
    producer_name: str | None = None
    region: str = Field(..., min_length=1)
    sub_region: str | None = None
    state: str | None = None
    altitude_min_masl: int | None = None
    altitude_max_masl: int | None = None
    notes: str | None = None

    def to_db_row(self, estate_id: str) -> dict[str, str | int | None]:
        return {
            "id": estate_id,
            "name": self.name,
            "producer_name": self.producer_name,
            "region": self.region,
            "sub_region": self.sub_region,
            "state": self.state,
            "altitude_min_masl": self.altitude_min_masl,
            "altitude_max_masl": self.altitude_max_masl,
            "notes": self.notes,
        }


class RawProductSnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore")

    roaster_id: str = Field(..., min_length=1)
    source_product_id: str = Field(..., min_length=1)
    source_url: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    body_html: str = ""
    tags: list[str] = Field(default_factory=list)
    product_type: str = ""
    content_hash: str = Field(..., min_length=16)
    scraped_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ParsedOffering(BaseModel):
    model_config = ConfigDict(extra="ignore")

    roaster_id: str = Field(..., min_length=1)
    estate_id: str | None = None
    estate_name: str | None = None
    source_product_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    source_url: str = Field(..., min_length=1)
    is_blend: bool = False
    region: str | None = None
    altitude_min_masl: int | None = None
    altitude_max_masl: int | None = None
    varietals: list[str] = Field(default_factory=list)
    process: str | None = None
    roast_level: str | None = None
    tasting_notes_raw: list[str] = Field(default_factory=list)
    flavor_categories: list[FlavorCategory] = Field(default_factory=list)
    in_stock: bool = True
    min_price_inr: float | None = None
    price_250g_inr: float | None = None
    extraction_confidence: ExtractionConfidence = "medium"
    content_hash: str = Field(..., min_length=16)

    @field_validator("flavor_categories", mode="before")
    @classmethod
    def deduplicate_and_validate_categories(cls, value: list[str]) -> list[str]:
        if not value:
            return []
        valid_set = set(FLAVOR_CATEGORIES)
        seen: set[str] = set()
        result: list[str] = []
        for item in value:
            if item not in valid_set:
                raise ValueError(
                    f"Invalid flavor category '{item}'. Must be one of {FLAVOR_CATEGORIES}."
                )
            if item not in seen:
                seen.add(item)
                result.append(item)
        return result

    @field_validator("varietals", "tasting_notes_raw", mode="before")
    @classmethod
    def deduplicate_string_list(cls, value: list[str]) -> list[str]:
        if not value:
            return []
        seen: set[str] = set()
        result: list[str] = []
        for item in value:
            cleaned = str(item).strip()
            key = cleaned.lower()
            if cleaned and key not in seen:
                seen.add(key)
                result.append(cleaned)
        return result