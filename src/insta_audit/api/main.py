import hmac
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse

from insta_audit.api.guards import InferenceGate, RateLimiter
from insta_audit.catalog import load_catalog
from insta_audit.decision import audit_drop, reconcile_order
from insta_audit.quality.expiry import evaluate_expiry, parse_expiry_details
from insta_audit.quality.freshness import FreshnessClassifier
from insta_audit.quality.ocr import ExpiryOCR
from insta_audit.schemas import (
    CatalogMatchRequest,
    CatalogMatchResponse,
    ExpiryRequest,
    ExpiryResponse,
    FreshnessRequest,
    FreshnessResponse,
    HealthResponse,
    PackingAuditRequest,
    PackingAuditResponse,
    ReconcileRequest,
)
from insta_audit.settings import get_settings
from insta_audit.versioning import build_versions
from insta_audit.vision.embedder import DinoEmbedder
from insta_audit.vision.sku_bank import SkuBank

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("insta_audit.api")
settings = get_settings()

app = FastAPI(title="Insta-Audit API", version="0.1.0", description="Edge-oriented store QC decision service")

if settings.environment == "production" and not settings.api_key and not settings.public_demo:
    raise RuntimeError(
        "INSTA_AUDIT_API_KEY is required when INSTA_AUDIT_ENVIRONMENT=production "
        "(or set INSTA_AUDIT_PUBLIC_DEMO=true for public showcase)"
    )

resolved_sku_bank_path = settings.resolve(settings.sku_bank_path)
resolved_catalog_path = settings.resolve(settings.catalog_path)
resolved_freshness_path = settings.resolve(settings.freshness_model_path)
resolved_embedding_path = settings.resolve(settings.embedding_model_path)
resolved_policy_path = Path(settings.resolve(settings.policy_path))

bank = SkuBank(resolved_sku_bank_path)
catalog = load_catalog(resolved_catalog_path)
freshness = FreshnessClassifier(resolved_freshness_path)
embedder = DinoEmbedder(resolved_embedding_path)
ocr = (
    ExpiryOCR(
        settings.resolve(settings.ocr_det_model_dir),
        settings.resolve(settings.ocr_rec_model_dir),
        settings.resolve(settings.ocr_cls_model_dir),
    )
    if settings.enable_ocr
    else None
)

quality_policy = {}
if resolved_policy_path.is_file():
    with resolved_policy_path.open(encoding="utf-8") as policy_file:
        quality_policy = yaml.safe_load(policy_file) or {}
else:
    logger.warning("Policy file not found at %s", resolved_policy_path)

# Fail-fast check for model & bank compatibility
if bank.ready and embedder.ready:
    if embedder.dimension and bank.vector_dim and embedder.dimension != bank.vector_dim:
        raise RuntimeError(
            f"Embedding model dimension mismatch: embedder produces {embedder.dimension}-d vectors, "
            f"but SKU bank requires {bank.vector_dim}-d vectors"
        )

rate_limiter = RateLimiter(settings.effective_rate_limit)
inference_gate = InferenceGate(settings.max_concurrent_inference, settings.inference_wait_seconds)


def get_system_versions() -> dict[str, str | None]:
    return build_versions(
        sku_bank_path=resolved_sku_bank_path,
        embedding_model_path=resolved_embedding_path,
        embedder_ready=embedder.ready,
        freshness_model_path=resolved_freshness_path,
        freshness_ready=freshness.ready,
        policy_path=resolved_policy_path,
        policy=quality_policy,
        catalog_path=resolved_catalog_path,
        ocr_ready=bool(ocr and ocr.ready),
    )


def log_structured_audit(event_type: str, **kwargs) -> None:
    payload = {"event_type": event_type, "timestamp": datetime.now(UTC).isoformat(), **kwargs}
    logger.info(json.dumps(payload))


def demo_file(filename: str) -> FileResponse:
    if settings.environment == "production" and not settings.demo_ui and not settings.public_demo:
        raise HTTPException(status_code=404, detail="demo UI is disabled in production")
    file_path = settings.root / filename
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail=f"file {filename} not found")
    return FileResponse(file_path)


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


def authenticate(request: Request, x_api_key: str | None = Header(default=None)) -> None:
    client_ip = request.client.host if request.client else "unknown"
    rate_limiter.check(client_ip)
    if settings.api_key:
        if not hmac.compare_digest(x_api_key or "", settings.api_key):
            raise HTTPException(status_code=401, detail="invalid API key")
    elif settings.environment == "production" and not settings.public_demo:
        raise HTTPException(status_code=401, detail="API key required in production")


@app.get("/health", response_model=HealthResponse, dependencies=[Depends(authenticate)])
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="insta-audit",
        environment=settings.environment,
        models={
            "sku_embedding_bank": bank.ready,
            "sku_embedder": embedder.ready,
            "freshness_classifier": freshness.ready,
            "expiry_ocr": bool(ocr and ocr.ready),
        },
        timestamp=datetime.now(UTC),
        versions=get_system_versions(),
    )


@app.get("/v1/catalog/skus", dependencies=[Depends(authenticate)])
def catalog_skus() -> dict:
    return {"skus": sorted(set(bank.skus.tolist())) if bank.ready else [], "model_ready": bank.ready}


@app.get("/v1/catalog/items", dependencies=[Depends(authenticate)])
def catalog_items() -> dict:
    return {"items": list(catalog.values()), "count": len(catalog)}


@app.post("/v1/catalog/match", response_model=CatalogMatchResponse, dependencies=[Depends(authenticate)])
def catalog_match(payload: CatalogMatchRequest) -> dict:
    start_time = time.perf_counter()
    max_bytes = settings.effective_max_image_bytes
    if len(payload.image_base64) > max_bytes * 4 // 3 + 8:
        raise HTTPException(status_code=413, detail="image exceeds configured size limit")
    versions = get_system_versions()
    if not bank.ready or not embedder.ready:
        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        return {
            "decision": "review_required",
            "top_sku": None,
            "similarity": None,
            "threshold": settings.min_visual_similarity,
            "reason_codes": ["sku_model_unavailable"],
            "model_ready": False,
            "versions": versions,
            "latency_ms": latency_ms,
        }
    with inference_gate.slot():
        vector = embedder.from_base64(payload.image_base64)
    result = bank.match(vector) if vector is not None else None
    latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
    if result is None:
        return {
            "decision": "review_required",
            "top_sku": None,
            "similarity": None,
            "threshold": settings.min_visual_similarity,
            "reason_codes": ["visual_embedding_unavailable"],
            "model_ready": True,
            "versions": versions,
            "latency_ms": latency_ms,
        }
    sku, score = result
    matched = score >= settings.min_visual_similarity
    res = {
        "decision": "match" if matched else "review_required",
        "top_sku": sku,
        "similarity": round(score, 4),
        "threshold": settings.min_visual_similarity,
        "reason_codes": ["catalog_match" if matched else "below_similarity_threshold"],
        "model_ready": True,
        "versions": versions,
        "latency_ms": latency_ms,
    }
    log_structured_audit(
        "catalog_match",
        top_sku=sku,
        similarity=res["similarity"],
        decision=res["decision"],
        latency_ms=latency_ms,
    )
    return res


@app.post("/v1/packing/audit", response_model=PackingAuditResponse, dependencies=[Depends(authenticate)])
def packing_audit(payload: PackingAuditRequest) -> dict:
    start_time = time.perf_counter()
    values = payload.model_dump()
    max_bytes = settings.effective_max_image_bytes
    if values["visual_embedding"] is None and values["crop_base64"]:
        if len(values["crop_base64"]) > max_bytes * 4 // 3 + 8:
            raise HTTPException(status_code=413, detail="image exceeds configured size limit")
        with inference_gate.slot():
            values["visual_embedding"] = embedder.from_base64(values["crop_base64"])
    values.pop("crop_base64", None)

    result = audit_drop(values, bank, settings.min_visual_similarity, settings.max_weight_error_pct)
    latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
    versions = get_system_versions()
    result["versions"] = versions
    result["latency_ms"] = latency_ms

    log_structured_audit(
        "packing_audit",
        event_id=result["event_id"],
        order_id=result["order_id"],
        expected_sku=result["expected_sku"],
        observed_sku=result["observed_sku"],
        decision=result["decision"],
        reason_codes=result["reason_codes"],
        visual_similarity=result["visual_similarity"],
        weight_error_pct=result["weight_error_pct"],
        versions=versions,
        latency_ms=latency_ms,
    )
    return result


@app.post("/v1/packing/reconcile", dependencies=[Depends(authenticate)])
def packing_reconcile(payload: ReconcileRequest) -> dict:
    result = reconcile_order(payload.expected_quantities, payload.observed_quantities)
    result["order_id"] = payload.order_id
    return result


@app.post("/v1/inward/expiry", response_model=ExpiryResponse, dependencies=[Depends(authenticate)])
def inward_expiry(payload: ExpiryRequest) -> dict:
    start_time = time.perf_counter()
    expiry = payload.expiry_date
    manufactured = payload.manufactured_date
    parsed = False
    ocr_text = payload.ocr_text
    max_bytes = settings.effective_max_image_bytes

    if not ocr_text and payload.image_base64 and ocr:
        if len(payload.image_base64) > max_bytes * 4 // 3 + 8:
            raise HTTPException(status_code=413, detail="image exceeds configured size limit")
        with inference_gate.slot():
            ocr_text = ocr.recognize_base64(payload.image_base64)

    if (expiry is None or manufactured is None) and ocr_text:
        details = parse_expiry_details(ocr_text, today=payload.received_date)
        if expiry is None:
            expiry = details.get("expiry")
            parsed = expiry is not None
        if manufactured is None:
            manufactured = details.get("manufactured")

    result = evaluate_expiry(
        payload.received_date,
        expiry,
        payload.category,
        quality_policy,
        manufactured=manufactured,
        sku=payload.sku,
    )
    latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
    versions = get_system_versions()

    res = {
        **result,
        "expiry_date": expiry,
        "manufactured_date": manufactured,
        "parsed_from_ocr": parsed,
        "batch_id": payload.batch_id,
        "versions": versions,
        "latency_ms": latency_ms,
    }

    log_structured_audit(
        "inward_expiry",
        batch_id=payload.batch_id,
        category=payload.category,
        sku=payload.sku,
        decision=res["decision"],
        reason_codes=res["reason_codes"],
        expiry_date=str(expiry) if expiry else None,
        manufactured_date=str(manufactured) if manufactured else None,
        life_basis=res.get("life_basis"),
        versions=versions,
        latency_ms=latency_ms,
    )
    return res


@app.post("/v1/inward/freshness", response_model=FreshnessResponse, dependencies=[Depends(authenticate)])
def inward_freshness(payload: FreshnessRequest) -> dict:
    start_time = time.perf_counter()
    max_bytes = settings.effective_max_image_bytes
    if len(payload.image_base64) > max_bytes * 4 // 3 + 8:
        raise HTTPException(status_code=413, detail="image exceeds configured size limit")
    with inference_gate.slot():
        result = freshness.predict_base64(payload.image_base64)
    latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
    versions = get_system_versions()
    result["versions"] = versions
    result["latency_ms"] = latency_ms

    log_structured_audit(
        "inward_freshness",
        sku=payload.sku,
        decision=result["decision"],
        class_name=result.get("class_name"),
        confidence=result.get("confidence"),
        reason_codes=result["reason_codes"],
        versions=versions,
        latency_ms=latency_ms,
    )
    return result
