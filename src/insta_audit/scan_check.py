"""Scan-vs-bag reconciliation (I1): audit the picker's barcode scans instead of repeating them."""

MISSING = {"quantity_short", "scanned_not_bagged"}
EXTRA = {"bagged_twice", "unexpected_item", "bagged_not_scanned"}


def reconcile_scan_vs_bag(
    ordered: dict[str, int],
    scanned: dict[str, int] | None,
    bagged: dict[str, int] | None,
    *,
    single_bag: bool = True,
) -> dict:
    """`ordered` is for this bag. `scanned` is order-level, so scan comparisons only run when the
    order has a single bag; multi-bag orders still get bag-vs-order checks."""
    if scanned is None and bagged is None:
        return {"status": "unavailable", "issues": {}, "codes": []}

    issues: dict[str, list[str]] = {}

    def flag(sku: str, code: str) -> None:
        issues.setdefault(sku, []).append(code)

    for sku in sorted(set(ordered) | set(scanned or {}) | set(bagged or {})):
        want = ordered.get(sku, 0)
        scan = (scanned or {}).get(sku, 0)
        bag = (bagged or {}).get(sku, 0)
        if bagged is not None:
            if want and bag < want:
                flag(sku, "quantity_short")
            if want and bag > want:
                flag(sku, "bagged_twice")
            if not want and bag:
                flag(sku, "unexpected_item")
        if scanned is not None and single_bag:
            if want and scan < want:
                flag(sku, "ordered_not_scanned")
            if not want and scan:
                flag(sku, "scanned_not_ordered")
            if bagged is not None and want:
                if scan > bag:
                    flag(sku, "scanned_not_bagged")
                if bag > scan and bag <= want:
                    flag(sku, "bagged_not_scanned")

    codes = sorted({code for sku_codes in issues.values() for code in sku_codes})
    return {"status": "mismatch" if issues else "ok", "issues": issues, "codes": codes}


def bag_evidence_direction(scan_result: dict) -> str | None:
    """'missing', 'extra', 'both' or None, from bag-count evidence (not picker-process issues)."""
    codes = set(scan_result.get("codes", []))
    missing, extra = bool(codes & MISSING), bool(codes & EXTRA)
    if missing and extra:
        return "both"
    return "missing" if missing else "extra" if extra else None
