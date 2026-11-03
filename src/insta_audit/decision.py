from insta_audit.vision.sku_bank import SkuBank


def audit_drop(payload: dict, bank: SkuBank, min_similarity: float, default_weight_error_pct: float) -> dict:
    expected_sku = payload["expected_sku"]
    observed_sku = payload.get("observed_sku")
    similarity = None
    model_ready = bank.ready
    reasons: list[str] = []

    if payload.get("visual_embedding") is not None:
        try:
            result = bank.match(payload["visual_embedding"])
            if result:
                observed_sku, similarity = result
        except ValueError:
            reasons.append("embedding_dimension_mismatch")

    quantity_delta = int(payload["observed_quantity"]) - int(payload["expected_quantity"])
    observed_weight = float(payload["measured_weight_delta_g"])
    expected_weight = float(payload["expected_unit_weight_g"]) * max(1, int(payload["observed_quantity"]))
    weight_error = abs(observed_weight - expected_weight) / expected_weight * 100.0
    tolerance = float(payload.get("max_weight_error_pct") or default_weight_error_pct)

    if quantity_delta < 0:
        reasons.append("quantity_short")
    elif quantity_delta > 0:
        reasons.append("quantity_over")
    if observed_sku and observed_sku != expected_sku:
        reasons.append("sku_mismatch")
    if similarity is not None and similarity < min_similarity:
        reasons.append("visual_match_below_threshold")
    if weight_error > tolerance:
        reasons.append("weight_out_of_tolerance")

    if reasons:
        decision = "hold"
    elif similarity is None:
        decision = "review_required"
        reasons.append("visual_similarity_unavailable")
    else:
        decision = "pass"
        reasons.append("sensor_checks_pass")

    return {
        "event_id": payload["event_id"],
        "order_id": payload["order_id"],
        "decision": decision,
        "reason_codes": reasons,
        "expected_sku": expected_sku,
        "observed_sku": observed_sku,
        "visual_similarity": round(similarity, 4) if similarity is not None else None,
        "weight_error_pct": round(weight_error, 2),
        "model_ready": model_ready,
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
