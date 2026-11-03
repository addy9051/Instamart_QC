"""One settled order/bag weighing at sealing (I4).

Expected weight is built from per-SKU GROSS weights (what a scale actually measures), with
tolerance = max(k * sigma_scale, relative %) so tiny and heavy packs are both judged sensibly.
Weights that are only a NET pack size are used with an extra bias allowance and can never
produce a high-confidence result.
"""
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class ScaleSpec:
    resolution_g: float = 1.0
    sigma_g: float = 2.0
    k: float = 3.0
    relative_tolerance_pct: float = 3.0
    nominal_bias_pct: float = 10.0
    uninformative_factor: float = 3.0


def _line_model(entry: dict | None, scale: ScaleSpec) -> dict | None:
    """Per-unit lo/hi/sigma/relative tolerance and basis, or None when no weight is known."""
    if not entry:
        return None
    pct = entry.get("weight_tolerance_pct") or scale.relative_tolerance_pct
    low, high = entry.get("min_weight_g"), entry.get("max_weight_g")
    if low and high:
        return {"lo": low, "hi": high, "sigma": 0.0, "pct": pct, "basis": "range"}
    gross = entry.get("gross_mean_g")
    if gross:
        sigma = entry.get("gross_std_g") or 0.0
        if entry.get("gross_verified"):
            return {"lo": gross, "hi": gross, "sigma": sigma, "pct": pct, "basis": "gross"}
        return {"lo": gross, "hi": gross, "sigma": sigma, "pct": pct + scale.nominal_bias_pct,
                "basis": "gross_unverified"}
    net = entry.get("unit_weight_g")
    if net:
        return {"lo": net, "hi": net, "sigma": 0.0, "pct": pct + scale.nominal_bias_pct, "basis": "nominal"}
    return None


def check_seal_weight(
    expected: dict[str, int],
    catalog: dict[str, dict],
    measured_g: float,
    scale: ScaleSpec,
    settled: bool = True,
) -> dict:
    result: dict = {
        "status": "uninformative",
        "measured_g": round(measured_g, 1),
        "expected_g": None,
        "lower_g": None,
        "upper_g": None,
        "tolerance_g": None,
        "delta_g": None,
        "confidence": "none",
        "basis": None,
        "suspected": [],
        "uninformative_skus": [],
        "unknown_skus": [],
        "inferred_unit_count": None,
    }
    if not settled:
        result["status"] = "unsettled"
        return result

    models: dict[str, dict] = {}
    unknown = []
    for sku in expected:
        model = _line_model(catalog.get(sku), scale)
        if model is None:
            unknown.append(sku)
        else:
            models[sku] = model
    if unknown:
        result["unknown_skus"] = sorted(unknown)
        return result

    low = high = variance = relative = blind = 0.0
    blind_skus = []
    for sku, qty in expected.items():
        model = models[sku]
        mid = (model["lo"] + model["hi"]) / 2
        low += qty * model["lo"]
        high += qty * model["hi"]
        variance += qty * model["sigma"] ** 2
        relative += qty * mid * model["pct"] / 100.0
        # Packs this light are within scale noise: the weight cannot see them, so their full
        # weight widens the band instead of silently failing every order that contains them.
        if model["hi"] < scale.uninformative_factor * scale.resolution_g:
            blind += qty * model["hi"]
            blind_skus.append(sku)

    tolerance = max(scale.k * math.sqrt(variance + scale.sigma_g**2), relative) + blind
    lower, upper = low - tolerance, high + tolerance
    expected_mid = (low + high) / 2
    bases = {m["basis"] for m in models.values()}
    solid = bases <= {"gross", "range"}

    result.update(
        expected_g=round(expected_mid, 1),
        lower_g=round(max(lower, 0.0), 1),
        upper_g=round(upper, 1),
        tolerance_g=round(tolerance, 1),
        delta_g=round(measured_g - expected_mid, 1),
        basis="gross" if solid else ("nominal" if bases == {"nominal"} else "mixed"),
        confidence="high" if solid else "low",
        uninformative_skus=sorted(blind_skus),
    )
    if len(blind_skus) == len(models):
        result["status"] = "uninformative"
        result["confidence"] = "none"
        return result

    if measured_g < lower:
        result["status"] = "short"
        result["suspected"] = _suspects(expected, models, expected_mid - measured_g, scale, blind_skus)
    elif measured_g > upper:
        result["status"] = "over"
        result["suspected"] = _suspects(expected, models, measured_g - expected_mid, scale, blind_skus)
    else:
        result["status"] = "ok"
    if len(models) == 1 and not blind_skus:
        model = next(iter(models.values()))
        result["inferred_unit_count"] = round(measured_g / ((model["lo"] + model["hi"]) / 2))
    return result


def _suspects(expected, models, gap_g: float, scale: ScaleSpec, blind_skus: list[str]) -> list[dict]:
    """SKUs whose whole units best explain the weight gap (a hint for the packer, not a verdict)."""
    candidates = []
    slack = max(scale.k * scale.sigma_g, gap_g * 0.15)
    for sku, qty in expected.items():
        if sku in blind_skus:
            continue
        unit = (models[sku]["lo"] + models[sku]["hi"]) / 2
        for units in range(1, qty + 1):
            error = abs(gap_g - units * unit)
            if error <= slack:
                candidates.append({"sku": sku, "units": units, "error_g": round(error, 1)})
    return sorted(candidates, key=lambda item: item["error_g"])[:3]
