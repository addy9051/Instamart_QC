import hmac
import logging
from datetime import UTC, datetime
from pathlib import Path

import yaml
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse

from insta_audit.catalog import load_catalog
from insta_audit.decision import audit_drop, reconcile_order
from insta_audit.quality.expiry import evaluate_expiry, parse_expiry
from insta_audit.quality.freshness import FreshnessClassifier
from insta_audit.quality.ocr import ExpiryOCR
from insta_audit.schemas import (
    ExpiryRequest,
    ExpiryResponse,
    CatalogMatchRequest,
    CatalogMatchResponse,
    FreshnessRequest,
    FreshnessResponse,
    HealthResponse,
    PackingAuditRequest,
    PackingAuditResponse,
    ReconcileRequest,
)
from insta_audit.settings import get_settings
from insta_audit.vision.sku_bank import SkuBank
from insta_audit.vision.embedder import DinoEmbedder

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("insta_audit.api")
settings = get_settings()
app = FastAPI(title="Insta-Audit API", version="0.1.0", description="Edge-oriented store QC decision service")
if settings.environment == "production" and not settings.api_key and not settings.demo_ui:
    raise RuntimeError("INSTA_AUDIT_API_KEY is required when INSTA_AUDIT_ENVIRONMENT=production")
bank = SkuBank(settings.sku_bank_path)
catalog = load_catalog(settings.catalog_path)
freshness = FreshnessClassifier(settings.freshness_model_path)
embedder = DinoEmbedder(settings.embedding_model_path)
ocr = ExpiryOCR(settings.ocr_det_model_dir, settings.ocr_rec_model_dir, settings.ocr_cls_model_dir) if settings.enable_ocr else None
policy_path = Path(__file__).resolve().parents[3] / "configs" / "quality_policy.yaml"
with policy_path.open(encoding="utf-8") as policy_file:
    quality_policy = yaml.safe_load(policy_file)
project_root = Path(__file__).resolve().parents[3]


def demo_file(filename: str) -> FileResponse:
    if settings.environment == "production" and not settings.demo_ui:
        raise HTTPException(status_code=404, detail="demo UI is disabled in production")
    return FileResponse(project_root / filename)


@app.get("/", include_in_schema=False)
def demo_home() -> FileResponse:
    return demo_file("index.html")


@app.get("/styles.css", include_in_schema=False)
def demo_styles() -> FileResponse:
    return demo_file("styles.css")


@app.get("/live.css", include_in_schema=False)
def demo_live_styles() -> FileResponse:
    return demo_file("live.css")


@app.get("/app.js", include_in_schema=False)
def demo_script() -> FileResponse:
    return demo_file("app.js")


def authenticate(x_api_key: str | None = Header(default=None)) -> None:
    if settings.api_key and not hmac.compare_digest(x_api_key or "", settings.api_key):
        raise HTTPException(status_code=401, detail="invalid API key")


@app.get("/health", response_model=HealthResponse, dependencies=[Depends(authenticate)])
def health() -> HealthResponse:
    return HealthResponse(
        status="ok", service="insta-audit", environment=settings.environment,
        models={"sku_embedding_bank": bank.ready, "sku_embedder": embedder.ready, "freshness_classifier": freshness.ready, "expiry_ocr": bool(ocr and ocr.ready)},
        timestamp=datetime.now(UTC),
    )


@app.get("/v1/catalog/skus", dependencies=[Depends(authenticate)])
def catalog_skus() -> dict:
    return {"skus": sorted(set(bank.skus.tolist())) if bank.ready else [], "model_ready": bank.ready}


@app.get("/v1/catalog/items", dependencies=[Depends(authenticate)])
def catalog_items() -> dict:
    return {"items": list(catalog.values()), "count": len(catalog)}


@app.post("/v1/catalog/match", response_model=CatalogMatchResponse, dependencies=[Depends(authenticate)])
def catalog_match(payload: CatalogMatchRequest) -> dict:
    if len(payload.image_base64) > settings.max_image_bytes * 4 // 3 + 8:
        raise HTTPException(status_code=413, detail="image exceeds configured size limit")
    if not bank.ready or not embedder.ready:
        return {"decision": "review_required", "top_sku": None, "similarity": None, "threshold": settings.min_visual_similarity, "reason_codes": ["sku_model_unavailable"], "model_ready": False}
    vector = embedder.from_base64(payload.image_base64)
    result = bank.match(vector) if vector is not None else None
    if result is None:
        return {"decision": "review_required", "top_sku": None, "similarity": None, "threshold": settings.min_visual_similarity, "reason_codes": ["visual_embedding_unavailable"], "model_ready": True}
    sku, score = result
    matched = score >= settings.min_visual_similarity
    return {"decision": "match" if matched else "review_required", "top_sku": sku, "similarity": round(score, 4), "threshold": settings.min_visual_similarity, "reason_codes": ["catalog_match" if matched else "below_similarity_threshold"], "model_ready": True}


@app.post("/v1/packing/audit", response_model=PackingAuditResponse, dependencies=[Depends(authenticate)])
def packing_audit(payload: PackingAuditRequest) -> dict:
    values = payload.model_dump()
    if values["visual_embedding"] is None and values["crop_base64"]:
        if len(values["crop_base64"]) > settings.max_image_bytes * 4 // 3 + 8:
            raise HTTPException(status_code=413, detail="image exceeds configured size limit")
        values["visual_embedding"] = embedder.from_base64(values["crop_base64"])
    values.pop("crop_base64", None)
    result = audit_drop(values, bank, settings.min_visual_similarity, settings.max_weight_error_pct)
    logger.info("packing decision=%s reasons=%s", result["decision"], ",".join(result["reason_codes"]))
    return result


@app.post("/v1/packing/reconcile", dependencies=[Depends(authenticate)])
def packing_reconcile(payload: ReconcileRequest) -> dict:
    result = reconcile_order(payload.expected_quantities, payload.observed_quantities)
    result["order_id"] = payload.order_id
    return result


@app.post("/v1/inward/expiry", response_model=ExpiryResponse, dependencies=[Depends(authenticate)])
def inward_expiry(payload: ExpiryRequest) -> dict:
    expiry = payload.expiry_date
    parsed = False
    ocr_text = payload.ocr_text
    if not ocr_text and payload.image_base64 and ocr:
        if len(payload.image_base64) > settings.max_image_bytes * 4 // 3 + 8:
            raise HTTPException(status_code=413, detail="image exceeds configured size limit")
        ocr_text = ocr.recognize_base64(payload.image_base64)
    if expiry is None and ocr_text:
        expiry = parse_expiry(ocr_text, today=payload.received_date)
        parsed = expiry is not None
    result = evaluate_expiry(payload.received_date, expiry, payload.category, quality_policy)
    logger.info("inward decision=%s reasons=%s", result["decision"], ",".join(result["reason_codes"]))
    return {**result, "expiry_date": expiry, "parsed_from_ocr": parsed, "batch_id": payload.batch_id}


@app.post("/v1/inward/freshness", response_model=FreshnessResponse, dependencies=[Depends(authenticate)])
def inward_freshness(payload: FreshnessRequest) -> dict:
    if len(payload.image_base64) > settings.max_image_bytes * 4 // 3 + 8:
        raise HTTPException(status_code=413, detail="image exceeds configured size limit")
    result = freshness.predict_base64(payload.image_base64)
    logger.info("freshness decision=%s reasons=%s", result["decision"], ",".join(result["reason_codes"]))
    return result


