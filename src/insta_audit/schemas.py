from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator


class PackingAuditRequest(BaseModel):
    event_id: str = Field(min_length=1, max_length=100)
    order_id: str = Field(min_length=1, max_length=100)
    expected_sku: str = Field(min_length=1, max_length=80)
    measured_weight_delta_g: float = Field(ge=0, le=100_000)
    expected_unit_weight_g: float = Field(gt=0, le=100_000)
    visual_embedding: list[float] | None = Field(default=None, min_length=8, max_length=8192)
    crop_base64: str | None = Field(default=None, max_length=7_000_000)
    observed_sku: str | None = Field(default=None, max_length=80)
    expected_quantity: int = Field(default=1, ge=1, le=100)
    observed_quantity: int = Field(default=1, ge=0, le=100)
    max_weight_error_pct: float | None = Field(default=None, ge=1, le=100)

    @field_validator("visual_embedding")
    @classmethod
    def finite_embedding(cls, vector: list[float] | None) -> list[float] | None:
        if vector is not None and any(not (-1e6 < value < 1e6) for value in vector):
            raise ValueError("embedding contains a non-finite or out-of-range value")
        return vector


class PackingAuditResponse(BaseModel):
    event_id: str
    order_id: str
    decision: Literal["pass", "hold", "review_required"]
    reason_codes: list[str]
    expected_sku: str
    observed_sku: str | None
    visual_similarity: float | None
    weight_error_pct: float
    model_ready: bool
    versions: dict[str, str | None] | None = None
    latency_ms: float | None = None


class ReconcileRequest(BaseModel):
    order_id: str = Field(min_length=1, max_length=100)
    expected_quantities: dict[str, Annotated[int, Field(ge=0, le=100)]] = Field(max_length=200)
    observed_quantities: dict[str, Annotated[int, Field(ge=0, le=100)]] = Field(max_length=200)


class SealRequest(BaseModel):
    """Everything the OMS already knows (lines, scans, expected weights) is looked up server-side
    by order_id; the client only reports what it measured."""

    order_id: str = Field(min_length=1, max_length=100)
    bag_id: str = Field(default="bag-1", min_length=1, max_length=40)
    measured_weight_g: float | None = Field(default=None, ge=0, le=100_000)
    settled: bool = True
    bagged_quantities: dict[str, Annotated[int, Field(ge=0, le=100)]] | None = Field(default=None, max_length=200)
    keyframes_base64: list[str] = Field(default_factory=list, max_length=8)
    seal_frame_base64: str | None = Field(default=None, max_length=7_000_000)


class SealResponse(BaseModel):
    order_id: str
    bag_id: str
    decision: Literal["pass", "hold", "review_required"]
    confident: bool
    mode: Literal["off", "shadow", "advisory", "enforce"]
    action: Literal["none", "notify", "block"]
    reason_codes: list[str]
    weight: dict | None = None
    scan: dict | None = None
    proof: dict | None = None
    versions: dict[str, str | None] | None = None
    latency_ms: float | None = None


class OrderWebhookResponse(BaseModel):
    status: Literal["accepted"]
    order_id: str


class ExpiryRequest(BaseModel):
    category: str = Field(min_length=1, max_length=50)
    received_date: date
    expiry_date: date | None = None
    manufactured_date: date | None = None
    sku: str | None = Field(default=None, max_length=80)
    ocr_text: str | None = Field(default=None, max_length=2_000)
    image_base64: str | None = Field(default=None, max_length=7_000_000)
    batch_id: str | None = Field(default=None, max_length=100)


class ExpiryResponse(BaseModel):
    decision: Literal["accept", "hold", "reject", "review_required"]
    reason_codes: list[str]
    expiry_date: date | None
    manufactured_date: date | None = None
    life_basis: str | None = None
    remaining_days: int | None
    remaining_life_pct: float | None
    required_remaining_life_pct: float
    parsed_from_ocr: bool
    batch_id: str | None
    versions: dict[str, str | None] | None = None
    latency_ms: float | None = None


class FreshnessRequest(BaseModel):
    sku: str = Field(min_length=1, max_length=80)
    image_base64: str = Field(min_length=32, max_length=7_000_000)


class FreshnessResponse(BaseModel):
    decision: Literal["accept", "hold", "reject", "review_required"]
    class_name: str | None
    confidence: float | None
    reason_codes: list[str]
    model_ready: bool
    versions: dict[str, str | None] | None = None
    latency_ms: float | None = None


class CatalogMatchRequest(BaseModel):
    image_base64: str = Field(min_length=32, max_length=7_000_000)


class CatalogMatchResponse(BaseModel):
    decision: Literal["match", "review_required"]
    top_sku: str | None
    similarity: float | None
    threshold: float
    reason_codes: list[str]
    model_ready: bool
    versions: dict[str, str | None] | None = None
    latency_ms: float | None = None


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    environment: str
    models: dict[str, bool]
    timestamp: datetime
    versions: dict[str, str | None] | None = None
