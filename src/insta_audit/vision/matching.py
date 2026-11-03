"""Open-set SKU matching rules (C8): top-k, similarity floor, and a margin over the runner-up."""
from dataclasses import dataclass


@dataclass(frozen=True)
class MatchPolicy:
    min_similarity: float = 0.5
    margin: float = 0.03
    # Lookalikes (same brand or same lookalike_group) score close together, so need a wider margin.
    lookalike_margin: float = 0.06


def _lookalikes(catalog: dict[str, dict], a: str, b: str) -> bool:
    left, right = catalog.get(a) or {}, catalog.get(b) or {}
    group = left.get("lookalike_group")
    if group and group == right.get("lookalike_group"):
        return True
    brand = (left.get("brand") or "").strip().lower()
    return bool(brand) and brand == (right.get("brand") or "").strip().lower()


def classify_match(
    topk: list[tuple[str, float]],
    policy: MatchPolicy,
    catalog: dict[str, dict] | None = None,
    expected_sku: str | None = None,
) -> dict:
    """status: confident | ambiguous | unknown | unavailable.

    confident = s1 >= tau and s1 - s2 >= margin. Below tau the item is treated as not in the bank
    (unknown), never forced onto the nearest SKU. `candidates` are the SKUs within the margin of s1,
    so callers can break ties with weight.
    """
    catalog = catalog or {}
    if not topk:
        return {"status": "unavailable", "top_sku": None, "similarity": None, "runner_up_sku": None,
                "runner_up_similarity": None, "margin": None, "threshold": policy.min_similarity,
                "candidates": []}
    top_sku, top_score = topk[0]
    runner_sku, runner_score = topk[1] if len(topk) > 1 else (None, None)
    threshold = (catalog.get(expected_sku or top_sku) or {}).get("match_threshold") or policy.min_similarity
    needed = policy.lookalike_margin if runner_sku and _lookalikes(catalog, top_sku, runner_sku) else policy.margin
    gap = None if runner_score is None else top_score - runner_score
    if top_score < threshold:
        status = "unknown"
    elif gap is not None and gap < needed:
        status = "ambiguous"
    else:
        status = "confident"
    candidates = [sku for sku, score in topk if top_score - score < needed and score >= threshold]
    return {
        "status": status,
        "top_sku": top_sku,
        "similarity": round(top_score, 4),
        "runner_up_sku": runner_sku,
        "runner_up_similarity": round(runner_score, 4) if runner_score is not None else None,
        "margin": round(gap, 4) if gap is not None else None,
        "threshold": threshold,
        "candidates": candidates,
    }
