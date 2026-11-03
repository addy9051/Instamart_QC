import base64
import binascii
import hmac
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import ValidationError

from insta_audit.api.guards import InferenceRunner, RateLimiter
from insta_audit.bag_audit import audit_bag
from insta_audit.catalog import load_catalog
from insta_audit.decision import audit_drop, reconcile_order
from insta_audit.modes import Mode, ModePolicy, apply_mode
from insta_audit.orders import (
    CompositeOrderSource,
    MockOrderSource,
    Order,
    WebhookOrderSource,
    verify_signature,
)
from insta_audit.privacy import PrivacyPolicy, parse_box, parse_boxes, redact_image
from insta_audit.proof import build_bundle, load_manifest, purge_expired, validate_id, verify_bundle
from insta_audit.quality.expiry import evaluate_expiry, parse_expiry_details
from insta_audit.quality.freshness import FreshnessClassifier
from insta_audit.quality.freshness_policy import CommodityPrototypes, FreshnessPolicy
from insta_audit.quality.ocr import ExpiryOCR
from insta_audit.schemas import (
    CatalogMatchRequest,
    CatalogMatchResponse,
    ExpiryRequest,
    ExpiryResponse,
    FreshnessRequest,
    FreshnessResponse,
    HealthResponse,
    OrderWebhookResponse,
    PackingAuditRequest,
    PackingAuditResponse,
    ReconcileRequest,
    SealRequest,
    SealResponse,
)
from insta_audit.seal import ScaleSpec
from insta_audit.settings import get_settings
from insta_audit.storage import LocalStore, ObjectStore, S3Store
from insta_audit.versioning import build_versions, file_digest
from insta_audit.vision.compat import check_compatibility
from insta_audit.vision.embedder import DinoEmbedder
from insta_audit.vision.matching import MatchPolicy, classify_match
from insta_audit.vision.onnx_embedder import OnnxEmbedder
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
freshness = FreshnessClassifier(
    resolved_freshness_path,
    FreshnessPolicy(
        cost_missed_rotten=settings.freshness_cost_missed_rotten,
        cost_false_reject=settings.freshness_cost_false_reject,
        accept_min_confidence=settings.freshness_accept_confidence,
        reject_min_probability=settings.freshness_reject_probability,
    ),
)
commodity_prototypes = CommodityPrototypes.load(settings.resolve(settings.commodity_prototypes_path))
embedder = (
    OnnxEmbedder(settings.resolve(settings.onnx_embedder_path), settings.onnx_image_size)
    if settings.onnx_embedder_path
    else DinoEmbedder(resolved_embedding_path)
)
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

compatibility = check_compatibility(
    bank_ready=bank.ready,
    bank_dim=bank.vector_dim,
    bank_model=bank.metadata.get("embedding_model"),
    embedder_ready=embedder.ready,
    embedder_dim=embedder.dimension,
    embedder_model=embedder.model_path,
)
if compatibility["status"] == "mismatch":
    logger.error("Embedder/SKU bank mismatch: %s", "; ".join(compatibility["reasons"]))
    if settings.strict_model_check:
        raise RuntimeError(
            "Embedder/SKU bank mismatch (set INSTA_AUDIT_STRICT_MODEL_CHECK=false to start anyway): "
            + "; ".join(compatibility["reasons"])
        )

rate_limiter = RateLimiter(settings.effective_rate_limit)
inference_runner = InferenceRunner(
    settings.max_concurrent_inference,
    settings.inference_max_queue,
    settings.inference_wait_seconds,
    settings.inference_timeout_seconds,
)

resolved_modes_path = settings.resolve(settings.modes_path)
mode_policy = ModePolicy.from_file(
    resolved_modes_path,
    allow_enforce=settings.allow_enforce,
    kill_switch=settings.kill_switch,
    default_mode=settings.default_mode,
)
scale_spec = ScaleSpec(
    resolution_g=settings.scale_resolution_g,
    sigma_g=settings.scale_sigma_g,
    k=settings.scale_k,
    relative_tolerance_pct=settings.seal_relative_tolerance_pct,
    nominal_bias_pct=settings.nominal_weight_bias_pct,
)
match_policy = MatchPolicy(
    min_similarity=settings.min_visual_similarity,
    margin=settings.match_margin,
    lookalike_margin=settings.lookalike_margin,
)
mock_orders = MockOrderSource.from_file(settings.resolve(settings.mock_orders_path))
webhook_orders = WebhookOrderSource(
    settings.webhook_secret, settings.webhook_result_url, settings.webhook_order_ttl_seconds
)
order_source = CompositeOrderSource(webhook_orders, mock_orders)

privacy_policy = PrivacyPolicy(
    retention_days=settings.proof_retention_days,
    roi=parse_box(settings.proof_roi),
    blur_boxes=parse_boxes(settings.proof_blur_boxes),
)


def build_proof_store() -> ObjectStore | None:
    if not settings.proof_enabled:
        return None
    if settings.proof_store == "s3":
        if not settings.proof_s3_bucket:
            raise RuntimeError("INSTA_AUDIT_PROOF_S3_BUCKET is required when INSTA_AUDIT_PROOF_STORE=s3")
        return S3Store(settings.proof_s3_bucket, settings.proof_s3_prefix, settings.proof_s3_endpoint_url)
    local_dir = Path(settings.proof_local_dir)
    return LocalStore(local_dir if local_dir.is_absolute() else settings.root / local_dir)


proof_store = build_proof_store()


def get_system_versions() -> dict[str, str | None]:
    versions = build_versions(
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
    modes_digest = file_digest(resolved_modes_path)
    versions["mode_policy"] = f"{mode_policy.version}@{modes_digest}" if modes_digest else None
    return versions


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
        status="degraded" if compatibility["status"] == "mismatch" else "ok",
        service="insta-audit",
        environment=settings.environment,
        models={
            "sku_embedding_bank": bank.ready,
            "sku_embedder": embedder.ready,
            "freshness_classifier": freshness.ready,
            "expiry_ocr": bool(ocr and ocr.ready),
        },
        compatibility=compatibility,
        embedding_failures=getattr(embedder, "failures", 0),
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
    vector = inference_runner.run(embedder.from_base64, payload.image_base64)
    topk = bank.match_topk(vector, k=3) if vector is not None else []
    latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
    if not topk:
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
    match = classify_match(topk, match_policy, catalog)
    matched = match["status"] == "confident"
    reason = {"confident": "catalog_match", "ambiguous": "ambiguous_lookalike", "unknown": "unknown_item"}[
        match["status"]
    ]
    res = {
        "decision": "match" if matched else "review_required",
        "top_sku": match["top_sku"],
        "similarity": match["similarity"],
        "runner_up_sku": match["runner_up_sku"],
        "runner_up_similarity": match["runner_up_similarity"],
        "margin": match["margin"],
        "match_status": match["status"],
        "threshold": match["threshold"],
        "reason_codes": [reason],
        "model_ready": True,
        "versions": versions,
        "latency_ms": latency_ms,
    }
    log_structured_audit(
        "catalog_match",
        top_sku=res["top_sku"],
        similarity=res["similarity"],
        margin=res["margin"],
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
        values["visual_embedding"] = inference_runner.run(embedder.from_base64, values["crop_base64"])
    values.pop("crop_base64", None)

    result = audit_drop(values, bank, catalog, scale_spec, match_policy)
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


def require_admin(request: Request, x_api_key: str | None = Header(default=None)) -> None:
    authenticate(request, x_api_key)
    if not settings.api_key and settings.environment != "development":
        raise HTTPException(status_code=403, detail="admin endpoints require an API key")


def _decode_proof_image(encoded: str) -> bytes:
    if len(encoded) > settings.effective_max_image_bytes * 4 // 3 + 8:
        raise HTTPException(status_code=413, detail="image exceeds configured size limit")
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise HTTPException(status_code=422, detail="image is not valid base64") from error


def _capture_proof(payload: SealRequest, order: Order, result: dict, mode: Mode, versions: dict) -> dict:
    images: list[tuple[str, bytes]] = []
    rejected = 0
    frames = [("keyframe", encoded) for encoded in payload.keyframes_base64]
    if payload.seal_frame_base64:
        frames.append(("seal", payload.seal_frame_base64))
    for kind, encoded in frames[: settings.proof_max_images]:
        try:
            images.append((kind, redact_image(_decode_proof_image(encoded), privacy_policy)))
        except ValueError:
            rejected += 1  # never store a frame that could not be redacted
    if not images and not result.get("weight"):
        return {"status": "skipped"}
    evidence = {
        "store_id": order.store_id,
        "ordered": order.expected_by_sku(payload.bag_id),
        "scans": order.scans,
        "bagged": payload.bagged_quantities,
        "seal_weight_g": payload.measured_weight_g,
        "weight": result.get("weight"),
        "scan_check": result.get("scan"),
        "decision": result["decision"],
        "reason_codes": result["reason_codes"],
        "mode": mode.label,
        "purpose": privacy_policy.purpose,
        "versions": versions,
    }
    manifest = build_bundle(
        proof_store, order.order_id, payload.bag_id, images, evidence, privacy_policy, settings.proof_signing_key
    )
    return {
        "status": "stored",
        "head_hash": manifest["head_hash"],
        "signed": bool(manifest["head_signature"]),
        "images": len(images),
        "images_rejected": rejected,
        "expires_at": manifest["expires_at"],
    }


@app.post("/v1/packing/seal", response_model=SealResponse, dependencies=[Depends(authenticate)])
def packing_seal(payload: SealRequest, background_tasks: BackgroundTasks) -> dict:
    """Order/bag-level audit at sealing: scan-vs-bag counts plus one settled weighing (I1, I4)."""
    start_time = time.perf_counter()
    try:
        validate_id(payload.order_id)
        validate_id(payload.bag_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    versions = get_system_versions()
    order = order_source.get_order(payload.order_id)

    if order is None:
        mode = mode_policy.resolve(None)
        result = {"order_id": payload.order_id, "bag_id": payload.bag_id, "decision": "review_required",
                  "confident": False, "reason_codes": ["order_not_found"], "weight": None, "scan": None}
    elif payload.bag_id not in order.bag_ids:
        mode = mode_policy.resolve(order.store_id)
        result = {"order_id": order.order_id, "bag_id": payload.bag_id, "decision": "review_required",
                  "confident": False, "reason_codes": ["bag_not_in_order"], "weight": None, "scan": None}
    else:
        mode = mode_policy.resolve(order.store_id, list(order.expected_by_sku(payload.bag_id)))
        if mode is Mode.OFF:
            result = {"order_id": order.order_id, "bag_id": payload.bag_id, "decision": "pass",
                      "confident": False, "reason_codes": ["mode_off"], "weight": None, "scan": None}
        else:
            result = audit_bag(
                order, payload.bag_id, catalog, scale_spec, payload.measured_weight_g,
                payload.settled, payload.bagged_quantities,
            )

    proof = None
    if proof_store is not None and order is not None and mode is not Mode.OFF and payload.bag_id in order.bag_ids:
        try:
            proof = _capture_proof(payload, order, result, mode, versions)
        except HTTPException:
            raise
        except Exception:
            logger.exception("proof-of-pack capture failed for %s/%s", payload.order_id, payload.bag_id)
            proof = {"status": "error"}

    action = apply_mode(result["decision"], result["confident"], mode)
    latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
    result.update(mode=mode.label, action=action, proof=proof, versions=versions, latency_ms=latency_ms)

    log_structured_audit(
        "bag_seal",
        order_id=result["order_id"],
        bag_id=result["bag_id"],
        store_id=order.store_id if order else None,
        mode=mode.label,
        action=action,
        decision=result["decision"],
        confident=result["confident"],
        reason_codes=result["reason_codes"],
        weight_status=(result["weight"] or {}).get("status"),
        proof_head_hash=(proof or {}).get("head_hash"),
        versions=versions,
        latency_ms=latency_ms,
    )
    # Shadow mode is never shown to packers, so nothing is posted back.
    if order is not None and mode in (Mode.ADVISORY, Mode.ENFORCE):
        background_tasks.add_task(order_source.post_result, order.order_id, {
            k: result[k] for k in ("bag_id", "decision", "mode", "action", "reason_codes")
        })
    return result


@app.post("/v1/orders/webhook", response_model=OrderWebhookResponse)
async def orders_webhook(
    request: Request,
    x_timestamp: str | None = Header(default=None),
    x_signature: str | None = Header(default=None),
) -> dict:
    """OMS "order ready to pack" push. Authenticated by HMAC signature, not the API key."""
    rate_limiter.check(request.client.host if request.client else "unknown")
    if not webhook_orders.enabled:
        raise HTTPException(status_code=503, detail="order webhook is not configured")
    body = await request.body()
    if len(body) > 256_000:
        raise HTTPException(status_code=413, detail="payload too large")
    if not verify_signature(webhook_orders.secret, x_timestamp or "", body, x_signature or ""):
        raise HTTPException(status_code=401, detail="invalid signature")
    try:
        order = Order.model_validate_json(body)
    except ValidationError as error:
        raise HTTPException(status_code=422, detail=error.errors(include_url=False, include_input=False)) from error
    try:
        validate_id(order.order_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    webhook_orders.ingest(order)
    return {"status": "accepted", "order_id": order.order_id}


@app.get("/v1/proof/{order_id}/{bag_id}", dependencies=[Depends(authenticate)])
def proof_lookup(order_id: str, bag_id: str) -> dict:
    """Manifest plus integrity verification for a stored proof-of-pack bundle."""
    if proof_store is None:
        raise HTTPException(status_code=404, detail="proof-of-pack is not enabled")
    try:
        manifest = load_manifest(proof_store, order_id, bag_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if manifest is None:
        raise HTTPException(status_code=404, detail="no proof bundle for this order/bag")
    return {"manifest": manifest, "verification": verify_bundle(proof_store, order_id, bag_id, settings.proof_signing_key)}


@app.post("/v1/admin/kill-switch", dependencies=[Depends(require_admin)])
def kill_switch(active: bool) -> dict:
    mode_policy.set_kill_switch(active)
    log_structured_audit("kill_switch", active=active)
    return {"kill_switch": mode_policy.kill_switch}


@app.post("/v1/admin/proof/purge", dependencies=[Depends(require_admin)])
def proof_purge() -> dict:
    if proof_store is None:
        raise HTTPException(status_code=404, detail="proof-of-pack is not enabled")
    removed = purge_expired(proof_store)
    log_structured_audit("proof_purge", removed=removed)
    return {"removed": removed}


def _store_expiry_override(payload: ExpiryRequest, ocr_text: str | None, ocr_expiry, disagrees: bool) -> dict:
    """Keep the label image and OCR text next to a manually typed expiry so it can be audited."""
    evidence = {
        "batch_id": payload.batch_id,
        "typed_expiry_date": str(payload.expiry_date),
        "ocr_text": (ocr_text or "")[:2000],
        "ocr_expiry_date": str(ocr_expiry) if ocr_expiry else None,
        "disagrees": disagrees,
        "image_stored": False,
        "stored": False,
    }
    if proof_store is None or not payload.batch_id:
        return evidence
    try:
        key = f"expiry_override/{validate_id(payload.batch_id)}/{int(time.time())}"
        if payload.image_base64:
            proof_store.put(f"{key}/label.jpg", _decode_proof_image(payload.image_base64), "image/jpeg")
            evidence["image_stored"] = True
        proof_store.put(f"{key}/evidence.json", json.dumps(evidence).encode("utf-8"), "application/json")
        evidence["stored"] = True
    except (ValueError, HTTPException):
        logger.warning("expiry override evidence not stored for batch %r", payload.batch_id)
    return evidence


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
        ocr_text = inference_runner.run(ocr.recognize_base64, payload.image_base64)

    ocr_details: dict = {}
    if ocr_text:
        ocr_details = parse_expiry_details(ocr_text, today=payload.received_date)
    ocr_expiry = ocr_details.get("expiry")
    manual_expiry = expiry is not None
    if expiry is None:
        expiry = ocr_expiry
        parsed = expiry is not None
    if manufactured is None:
        manufactured = ocr_details.get("manufactured")

    result = evaluate_expiry(
        payload.received_date,
        expiry,
        payload.category,
        quality_policy,
        manufactured=manufactured,
        sku=payload.sku,
    )

    # A typed date overrides OCR; keep the evidence and flag disagreement (C15).
    disagrees = bool(manual_expiry and ocr_expiry and ocr_expiry != expiry)
    override_evidence = None
    if manual_expiry and (ocr_text or payload.image_base64):
        if disagrees:
            result["reason_codes"] = [*result["reason_codes"], "manual_expiry_disagrees_with_ocr"]
            if result["decision"] == "accept":
                result["decision"] = "review_required"
        override_evidence = _store_expiry_override(payload, ocr_text, ocr_expiry, disagrees)
    latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
    versions = get_system_versions()

    res = {
        **result,
        "expiry_date": expiry,
        "manufactured_date": manufactured,
        "parsed_from_ocr": parsed,
        "expiry_source": "manual" if manual_expiry else ("ocr" if parsed else "none"),
        "ocr_expiry_date": ocr_expiry,
        "ocr_disagrees": disagrees,
        "override_evidence": override_evidence,
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
        expiry_source=res["expiry_source"],
        ocr_text=(ocr_text or "")[:200] if manual_expiry else None,
        ocr_disagrees=disagrees,
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
    commodity_check = None
    if commodity_prototypes is not None and embedder.ready:
        vector = inference_runner.run(embedder.from_base64, payload.image_base64)
        if vector is not None:
            requested = (catalog.get(payload.sku) or {}).get("commodity") or payload.sku
            commodity_check = commodity_prototypes.check(vector, requested, settings.ood_min_similarity)
    if commodity_check and commodity_check["status"] != "ok":
        result = {"decision": "review_required", "action": "review", "class_name": None, "confidence": None,
                  "reason_codes": [commodity_check["reason"]], "model_ready": freshness.ready}
    else:
        result = inference_runner.run(freshness.predict_base64, payload.image_base64)
    result["commodity_check"] = commodity_check
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
