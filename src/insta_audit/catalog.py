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
        catalog[sku] = {
            "sku": sku,
            "name": row.get("name") or sku,
            "brand": row.get("brand") or "",
            "category": row.get("category") or "packaged",
            "unit_weight_g": weight,
            "weight_source": "verified" if verified else ("nominal_from_pack_size" if weight else "unknown"),
        }
    return catalog


