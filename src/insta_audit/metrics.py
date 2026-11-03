"""Per-order evaluation metrics (I3). Items don't ship alone: a per-item false-hold rate of 15% is
~70% of 8-item orders disrupted. Gate releases on these per-order numbers instead."""
import random
from collections.abc import Sequence

DEFAULT_MAX_FALSE_HOLD_RATE = 0.01


def percentile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[index]


def bootstrap_ci(
    flags: Sequence[bool], resamples: int = 1000, confidence: float = 0.95, seed: int = 7
) -> tuple[float, float] | None:
    """Percentile bootstrap CI for a proportion."""
    if not flags:
        return None
    rng = random.Random(seed)
    n = len(flags)
    rates = sorted(sum(flags[rng.randrange(n)] for _ in range(n)) / n for _ in range(resamples))
    tail = (1 - confidence) / 2
    return rates[int(tail * resamples)], rates[min(resamples - 1, int((1 - tail) * resamples))]


def _rate(flags: list[bool]) -> dict:
    ci = bootstrap_ci(flags)
    return {
        "n": len(flags),
        "rate": round(sum(flags) / len(flags), 4) if flags else None,
        "ci95": [round(ci[0], 4), round(ci[1], 4)] if ci else None,
    }


def order_report(
    orders: Sequence[dict],
    added_seconds: Sequence[float] | None = None,
    max_false_hold_rate: float = DEFAULT_MAX_FALSE_HOLD_RATE,
) -> dict:
    """Each order: {"has_error": bool, "decision": "pass"|"hold"|"review_required"}.

    False hold: error-free order held. False review: error-free order sent to review.
    Miss: order with a real error that passed. Gate uses the upper CI bound, not the point estimate.
    """
    clean = [o for o in orders if not o["has_error"]]
    bad = [o for o in orders if o["has_error"]]
    false_hold = _rate([o["decision"] == "hold" for o in clean])
    report = {
        "orders": len(orders),
        "clean_orders": len(clean),
        "error_orders": len(bad),
        "false_hold_rate": false_hold,
        "false_review_rate": _rate([o["decision"] == "review_required" for o in clean]),
        "error_miss_rate": _rate([o["decision"] == "pass" for o in bad]),
        "error_caught_as_hold_rate": _rate([o["decision"] == "hold" for o in bad]),
    }
    if added_seconds:
        report["added_seconds"] = {
            "p50": percentile(added_seconds, 0.5),
            "p95": percentile(added_seconds, 0.95),
        }
    upper = false_hold["ci95"][1] if false_hold["ci95"] else None
    report["gate"] = {
        "max_false_hold_rate": max_false_hold_rate,
        "passed": upper is not None and upper <= max_false_hold_rate,
        "basis": "upper 95% CI bound of per-order false-hold rate",
    }
    return report


def miss_rate_at_false_hold_budget(
    scored: Sequence[tuple[bool, float]], budget: float
) -> float | None:
    """`scored`: (has_error, anomaly score; higher = more suspicious). Picks the lowest threshold
    whose false-hold rate on clean orders stays within `budget`, then reports the miss rate."""
    clean = sorted(score for error, score in scored if not error)
    bad = [score for error, score in scored if error]
    if not clean or not bad:
        return None
    allowed = int(budget * len(clean))
    threshold = clean[-allowed - 1] if allowed < len(clean) else float("-inf")
    return sum(score <= threshold for score in bad) / len(bad)


def exposure(false_hold_per_item: float, items: int) -> float:
    """Chance an order of `items` lines has at least one false hold."""
    return 1 - (1 - false_hold_per_item) ** items
