"""Order/bag-level decision matrix (I3).

confident mismatch -> hold; uncertain or single-signal -> review_required; consistent -> pass.
A hold needs two independent signals (bag count and weight) pointing the same way, with weight
based on verified gross weights. Everything else is at most a review.
"""
from insta_audit.orders import Order
from insta_audit.scan_check import bag_evidence_direction, reconcile_scan_vs_bag
from insta_audit.seal import ScaleSpec, check_seal_weight


def audit_bag(
    order: Order,
    bag_id: str,
    catalog: dict[str, dict],
    scale: ScaleSpec,
    measured_weight_g: float | None,
    settled: bool = True,
    bagged: dict[str, int] | None = None,
) -> dict:
    ordered = order.expected_by_sku(bag_id)
    single_bag = len(order.bag_ids) == 1
    scan = reconcile_scan_vs_bag(ordered, order.scans, bagged, single_bag=single_bag)
    if measured_weight_g is None:
        weight = {"status": "unavailable", "confidence": "none"}
    else:
        weight = check_seal_weight(ordered, catalog, measured_weight_g, scale, settled)

    reasons: list[str] = []
    direction = bag_evidence_direction(scan)
    weight_direction = {"short": "missing", "over": "extra"}.get(weight["status"])
    weight_bad = weight_direction is not None

    reasons += [f"scan_{code}" for code in scan["codes"]]
    if weight["status"] in ("short", "over"):
        reasons.append(f"weight_{weight['status']}")
    elif weight["status"] == "unsettled":
        reasons.append("scale_unsettled")
    elif weight["status"] == "uninformative":
        reasons.append("weight_uninformative")
    elif weight["status"] == "unavailable":
        reasons.append("weight_unavailable")
    if weight_bad and weight["confidence"] == "low":
        reasons.append("weight_basis_not_verified")

    confident = False
    if direction in ("missing", "extra") and direction == weight_direction and weight["confidence"] == "high":
        decision, confident = "hold", True
        reasons.append("count_and_weight_agree")
    elif direction or weight_bad or scan["status"] == "mismatch":
        decision = "review_required"
    elif weight["status"] != "ok" and bagged is None:
        # Weight can't vouch for the bag and nothing counted what went into it.
        decision = "review_required"
        reasons.append("no_usable_evidence")
    else:
        decision = "pass"
        reasons.append("seal_checks_pass")

    return {
        "order_id": order.order_id,
        "bag_id": bag_id,
        "decision": decision,
        "confident": confident,
        "reason_codes": reasons,
        "weight": weight,
        "scan": scan,
    }
