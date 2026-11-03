"""Demo catalog loader. Weights parsed from pack-size text are NOMINAL, not scale-calibrated."""
import re
from pathlib import Path

import yaml

_QTY = re.compile(r"(\d+(?:\.\d+)?)\s*(kg|g|gm|gr|gram|grams|ml|l|litre|liter|litter)\b", re.I)
_TO_GRAMS = {"kg": 1000, "g": 1, "gm": 1, "gr": 1, "gram": 1, "grams": 1,
             "ml": 1, "l": 1000, "litre": 1000, "liter": 1000, "litter": 1000}


def nominal_weight_g(quantity: str | None) -> float | None:
    """Parse '52 g' / '500ml' / '1 L' into grams (1 ml ~ 1 g). None if not parseable."""
    match = _QTY.search(quantity or "")
    if not match:
        return None
    grams = float(match.group(1)) * _TO_GRAMS[match.group(2).lower()]
    return grams if 5 <= grams <= 5000 else None


def load_catalog(path: str | Path) -> dict[str, dict]:
    path = Path(path)
    if not path.is_file():
        return {}
    rows = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("skus", [])
    catalog: dict[str, dict] = {}
    for row in rows:
        sku = str(row["sku"])
        verified = row.get("expected_unit_weight_g")
        weight = verified if verified else nominal_weight_g(row.get("catalog_quantity"))
        gross_mean = _positive(row.get("gross_mean_g"))
        gross_std = _positive(row.get("gross_std_g"), allow_zero=True)
        min_weight = _positive(row.get("min_weight_g"))
        max_weight = _positive(row.get("max_weight_g"))
        if min_weight and max_weight and min_weight > max_weight:
            raise ValueError(f"SKU {sku}: min_weight_g exceeds max_weight_g")
        catalog[sku] = {
            "sku": sku,
            "name": row.get("name") or sku,
            "brand": row.get("brand") or "",
            "category": row.get("category") or "packaged",
            "unit_weight_g": weight,
            # `weight_verified` in older catalog files only ever meant a net pack size; the scale
            # measures gross weight, so only gross_verified (real weighings) counts as verified.
            "weight_source": (
                "gross_verified" if gross_mean and row.get("gross_verified")
                else "net_pack_size" if verified
                else "nominal_from_pack_size" if weight else "unknown"
            ),
            # Seal-weight fields. gross_* come from weighing real units; unit_weight_g above may be
            # a NET pack-size weight and is only used as a wide, advisory-only fallback.
            "gross_mean_g": gross_mean,
            "gross_std_g": gross_std,
            "gross_verified": bool(row.get("gross_verified")) and gross_mean is not None,
            "min_weight_g": min_weight,
            "max_weight_g": max_weight,
            "weight_tolerance_pct": _positive(row.get("weight_tolerance_pct")),
            # Per-SKU visual similarity floor and lookalike grouping, tuned on real captures.
            "match_threshold": _positive(row.get("match_threshold")),
            "lookalike_group": row.get("lookalike_group"),
            "commodity": row.get("commodity"),
        }
    return catalog


def _positive(value, allow_zero: bool = False) -> float | None:
    if value is None:
        return None
    number = float(value)
    if number < 0 or (number == 0 and not allow_zero):
        raise ValueError(f"expected a positive number, got {value!r}")
    return number


