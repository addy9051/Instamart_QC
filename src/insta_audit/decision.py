"""Per-item drop audit. Identity and weight expectations come from the server-side catalog by
`expected_sku`; the client supplies only what it measured (weight delta, embedding/crop)."""
from insta_audit.seal import ScaleSpec, check_seal_weight
from insta_audit.vision.matching import MatchPolicy, classify_match
from insta_audit.vision.sku_bank import SkuBank


def audit_drop(
    payload: dict,
    bank: SkuBank,
    catalog: dict[str, dict],
    scale: ScaleSpec,
    policy: MatchPolicy,
) -> dict:
    expected_sku = payload["expected_sku"]
    expected_qty = int(payload.get("expected_quantity", 1))
    measured = float(payload["measured_weight_delta_g"])
    hard: list[str] = []  # confident problems that can justify a hold
    soft: list[str] = []  # uncertain signals: review, never a hold

    weight = check_seal_weight(
        {expected_sku: expected_qty}, catalog, measured, scale, bool(payload.get("settled", True))
    )
    inferred = weight["inferred_unit_count"]
    confident_weight = weight["confidence"] == "high"
    weight_error_pct = None
    if weight["expected_g"]:
        weight_error_pct = round(abs(weight["delta_g"]) / weight["expected_g"] * 100.0, 2)

    if weight["status"] in ("short", "over"):
        bucket = hard if confident_weight else soft
        bucket.append("weight_out_of_tolerance" if confident_weight else "weight_out_of_tolerance_unverified")
        # The count is inferred from weight, not reported by the client.
        if inferred is not None and inferred != expected_qty:
            bucket.append("quantity_short" if inferred < expected_qty else "quantity_over")
    elif weight["status"] == "unsettled":
        soft.append("scale_unsettled")
    elif weight["status"] == "uninformative":
        soft.append("catalog_weight_unavailable" if weight["unknown_skus"] else "weight_uninformative")

    match = classify_match([], policy)
    observed_sku = None
    reasons_vision: list[str] = []
    resolved_by_weight = False
    embedding = payload.get("visual_embedding")
    if embedding is not None:
        try:
            match = classify_match(bank.match_topk(embedding, k=3), policy, catalog, expected_sku)
        except ValueError:
            soft.append("embedding_dimension_mismatch")
    if match["status"] == "confident":
        observed_sku = match["top_sku"]
        if observed_sku != expected_sku:
            hard.append("sku_mismatch")
    elif match["status"] == "ambiguous":
        candidates = match["candidates"]
        fits = [
            sku for sku in candidates
            if check_seal_weight({sku: expected_qty}, catalog, measured, scale)["status"] == "ok"
        ]
        if fits == [expected_sku]:
            observed_sku, resolved_by_weight = expected_sku, True
            reasons_vision.append("visual_ambiguous_resolved_by_weight")
        else:
            soft.append("visual_ambiguous_lookalike")
    elif match["status"] == "unknown":
        soft.append("unknown_item")

    if hard:
        decision, reasons = "hold", hard + soft
    elif soft:
        decision, reasons = "review_required", soft
    elif match["status"] == "unavailable":
        decision, reasons = "review_required", ["visual_similarity_unavailable"]
    else:
        decision, reasons = "pass", ["sensor_checks_pass"]
    reasons = reasons + reasons_vision

    return {
        "event_id": payload["event_id"],
        "order_id": payload["order_id"],
        "decision": decision,
        "reason_codes": reasons,
        "expected_sku": expected_sku,
        "observed_sku": observed_sku,
        "visual_similarity": match["similarity"],
        "visual_margin": match["margin"],
        "match_status": match["status"],
        "resolved_by_weight": resolved_by_weight,
        "inferred_quantity": inferred,
        "weight_status": weight["status"],
        "weight_error_pct": weight_error_pct,
        "model_ready": bank.ready,
    }


def reconcile_order(expected: dict[str, int], observed: dict[str, int]) -> dict:
    differences = {
        sku: {"expected": int(count), "observed": int(observed.get(sku, 0))}
        for sku, count in expected.items()
        if int(observed.get(sku, 0)) != int(count)
    }
    unknown = {sku: int(count) for sku, count in observed.items() if sku not in expected and count > 0}
    if unknown:
        differences.update({sku: {"expected": 0, "observed": count} for sku, count in unknown.items()})
    return {"decision": "pass" if not differences else "hold", "differences": differences}
