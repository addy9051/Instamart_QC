"""Synthetic per-order evaluation of the seal-time decision (no ML models needed).

Simulates multi-item orders with a realistic noise model (per-SKU gross bias and spread, scale
noise, bag jitter) and injected errors (missing unit, duplicate unit, wrong item), runs the REAL
`audit_bag`, and reports PER-ORDER rates with bootstrap CIs.

This is a design-time sanity check on synthetic data, NOT store performance. Replace the noise
model with real weighings (see docs/product_direction.md) before trusting any number.
"""
import argparse
import json
import random
from pathlib import Path

from insta_audit.bag_audit import audit_bag
from insta_audit.catalog import load_catalog
from insta_audit.metrics import exposure, order_report
from insta_audit.orders import Order, OrderLine
from insta_audit.seal import ScaleSpec


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default="data/catalog/skus.yaml")
    ap.add_argument("--orders", type=int, default=2000)
    ap.add_argument("--items", type=int, default=8)
    ap.add_argument("--error-rate", type=float, default=0.15)
    ap.add_argument("--gross-bias-pct", type=float, default=6.0)
    ap.add_argument("--sku-spread-pct", type=float, default=1.5)
    ap.add_argument("--scale-sigma-g", type=float, default=2.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--output", type=Path, default=Path("docs/seal_eval_results.json"))
    args = ap.parse_args()

    rng = random.Random(args.seed)
    scale = ScaleSpec(sigma_g=args.scale_sigma_g)
    base = {sku: v for sku, v in load_catalog(args.catalog).items() if v["unit_weight_g"]}
    skus = sorted(base)

    # Pretend each SKU was weighed N times: the "verified" catalog is the measured gross mean/std.
    truth, catalog = {}, {}
    for sku in skus:
        gross = base[sku]["unit_weight_g"] * (1 + args.gross_bias_pct / 100) * rng.uniform(0.97, 1.03)
        spread = gross * args.sku_spread_pct / 100
        truth[sku] = (gross, spread)
        catalog[sku] = {**base[sku], "gross_mean_g": gross, "gross_std_g": spread, "gross_verified": True}

    def weigh(counts: dict[str, int]) -> float:
        total = sum(sum(rng.gauss(truth[s][0], truth[s][1]) for _ in range(n)) for s, n in counts.items())
        return max(0.0, total + rng.gauss(0, args.scale_sigma_g))

    results = []
    for index in range(args.orders):
        picked = rng.sample(skus, min(args.items, len(skus)))
        lines = {sku: rng.randint(1, 2) for sku in picked}
        packed = dict(lines)
        kind = None
        if rng.random() < args.error_rate:
            kind = rng.choice(["missing_unit", "duplicate_unit", "wrong_item"])
            target = rng.choice(picked)
            if kind == "missing_unit":
                packed[target] -= 1
            elif kind == "duplicate_unit":
                packed[target] += 1
            else:
                packed[target] -= 1
                swap = rng.choice([s for s in skus if s not in lines])
                packed[swap] = packed.get(swap, 0) + 1
        packed = {s: n for s, n in packed.items() if n > 0}
        order = Order(order_id=f"S{index}", store_id="SIM",
                      lines=[OrderLine(sku=s, quantity=n) for s, n in lines.items()], scans=dict(lines))
        # The vision/tracker count is modelled as noisy: it sees the true bag 90% of the time.
        seen = packed if rng.random() < 0.9 else dict(lines)
        outcome = audit_bag(order, "bag-1", catalog, scale, weigh(packed), True, seen)
        results.append({"has_error": kind is not None, "kind": kind, "decision": outcome["decision"]})

    report = order_report(results)
    report["by_error_kind"] = {
        kind: {"orders": sum(r["kind"] == kind for r in results),
               "held": sum(r["kind"] == kind and r["decision"] == "hold" for r in results),
               "reviewed": sum(r["kind"] == kind and r["decision"] == "review_required" for r in results),
               "passed": sum(r["kind"] == kind and r["decision"] == "pass" for r in results)}
        for kind in ("missing_unit", "duplicate_unit", "wrong_item")
    }
    report["disclaimer"] = "Synthetic noise model; not store performance."
    report["items_per_order"] = args.items
    report["per_item_to_per_order_example"] = {
        "per_item_false_hold": 0.148, "order_exposure": round(exposure(0.148, args.items), 3),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
