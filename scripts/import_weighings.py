"""Turn real weighings into catalog gross-weight fields (C6).

Input CSV columns: sku,unit_id,batch,weight_g,scale_id,session_id,timestamp (extra columns ignored).
Per SKU this writes gross_mean_g and gross_std_g, and sets gross_verified only when there are at
least --min-units weighings from at least --min-batches batches (default 20 units, 1 batch). It never
touches expected_unit_weight_g (the NET pack size).

    python scripts/import_weighings.py --weighings data/validation/weighings.csv
"""
import argparse
import csv
import statistics
from collections import defaultdict
from pathlib import Path

import yaml


def summarise(rows: list[dict], min_units: int, min_batches: int) -> dict[str, dict]:
    by_sku: dict[str, list[float]] = defaultdict(list)
    batches: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        weight = float(row["weight_g"])
        if weight <= 0:
            raise ValueError(f"non-positive weight for {row.get('sku')}: {weight}")
        by_sku[row["sku"].strip()].append(weight)
        batches[row["sku"].strip()].add((row.get("batch") or "").strip() or "unknown")
    summary = {}
    for sku, weights in by_sku.items():
        n = len(weights)
        summary[sku] = {
            "gross_mean_g": round(statistics.fmean(weights), 2),
            "gross_std_g": round(statistics.stdev(weights), 3) if n > 1 else None,
            "gross_verified": n >= min_units and len(batches[sku]) >= min_batches,
            "units": n,
            "batches": len(batches[sku]),
        }
    return summary


def apply_to_catalog(catalog: dict, summary: dict[str, dict]) -> tuple[list[str], list[str]]:
    updated, unknown = [], []
    known = {str(row["sku"]): row for row in catalog.get("skus", [])}
    for sku, stats in summary.items():
        row = known.get(sku)
        if row is None:
            unknown.append(sku)
            continue
        row["gross_mean_g"] = stats["gross_mean_g"]
        row["gross_std_g"] = stats["gross_std_g"]
        row["gross_verified"] = stats["gross_verified"]
        updated.append(sku)
    return updated, unknown


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--weighings", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=Path("data/catalog/skus.yaml"))
    parser.add_argument("--min-units", type=int, default=20)
    parser.add_argument("--min-batches", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with args.weighings.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise SystemExit("no weighings found")
    catalog = yaml.safe_load(args.catalog.read_text(encoding="utf-8")) or {}
    summary = summarise(rows, args.min_units, args.min_batches)
    updated, unknown = apply_to_catalog(catalog, summary)
    for sku in updated:
        s = summary[sku]
        flag = "verified" if s["gross_verified"] else f"NOT verified (<{args.min_units} units or <{args.min_batches} batches)"
        spread = f"{s['gross_std_g'] / s['gross_mean_g'] * 100:.1f}%" if s["gross_std_g"] else "n/a"
        print(f"{sku}: n={s['units']} batches={s['batches']} mean={s['gross_mean_g']} g std={s['gross_std_g']} ({spread}) {flag}")
    if unknown:
        print(f"Ignored SKUs not in the catalog: {', '.join(sorted(unknown))}")
    if not args.dry_run:
        args.catalog.write_text(yaml.safe_dump(catalog, sort_keys=False, allow_unicode=True), encoding="utf-8")
        print(f"Updated {len(updated)} SKUs in {args.catalog}")


if __name__ == "__main__":
    main()
