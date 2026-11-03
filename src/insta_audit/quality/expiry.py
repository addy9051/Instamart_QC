import re
from calendar import monthrange
from datetime import date, timedelta

MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

# Ordered most-specific first; overlapping lower-priority matches are discarded.
_DATE_PATTERNS = [
    ("ymd", re.compile(r"\b(\d{4})[/.\-](\d{1,2})[/.\-](\d{1,2})\b")),
    ("dmy_num", re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4}|\d{2})\b")),
    ("dmy_word", re.compile(r"\b(\d{1,2})[ /.\-]?([A-Z]{3})[A-Z]*[ ,./'\-]*(\d{4}|\d{2})\b")),
    ("mon_year", re.compile(r"\b([A-Z]{3})[A-Z]*[ ,./'\-]*(\d{4}|\d{2})\b")),
    ("my", re.compile(r"\b(\d{1,2})[/.\-](\d{4}|\d{2})\b")),
]
_EXPIRY_KW = re.compile(
    r"\b(?:EXP(?:IRY|IRES|IRATION)?(?:\s*DATE)?|USE\s*BY|USE\s*BEFORE|BEST\s*BEFORE(?:\s*END)?|BBE?)\b"
)
_MFG_KW = re.compile(
    r"\b(?:DATE\s*OF\s*(?:MFG|MANUFACTURE|MANUFACTURING|PACKING|PACKAGING)|MFGD?|MFD|"
    r"MANUFACTURED(?:\s*ON)?|MANUFACTURE|MANUFACTURING|PACKED(?:\s*ON)?|PACKAGING|PACKING|PKD|PKG|DOM|DOP)\b"
)
_RELATIVE = re.compile(
    r"(?:BEST\s*BEFORE|USE\s*WITHIN|CONSUME\s*WITHIN|EXPIRES?\s*IN|SHELF\s*LIFE)[^0-9]{0,20}?"
    r"(\d{1,3})\s*(DAYS?|WEEKS?|MONTHS?|MTHS?|YEARS?|YRS?)\b"
)
_KEYWORD_WINDOW = 40  # max characters between a keyword and the date it labels


def _year(value: int) -> int:
    return value + (2000 if value < 70 else 1900) if value < 100 else value


def _to_date(kind: str, groups: tuple[str, ...]) -> date | None:
    try:
        if kind == "ymd":
            year, month, day = map(int, groups)
        elif kind == "dmy_num":
            day, month, year = map(int, groups)
        elif kind == "dmy_word":
            day, month, year = int(groups[0]), MONTHS[groups[1]], int(groups[2])
        elif kind == "mon_year":
            month, year, day = MONTHS[groups[0]], int(groups[1]), 0
        else:  # my: month/year, printed without a day -> end of month
            month, year, day = int(groups[0]), int(groups[1]), 0
        year = _year(year)
        if day == 0:
            day = monthrange(year, month)[1]
        return date(year, month, day)
    except (ValueError, KeyError, OverflowError):
        return None


def _add_duration(start: date, amount: int, unit: str) -> date:
    if unit.startswith("D"):
        return start + timedelta(days=amount)
    if unit.startswith("W"):
        return start + timedelta(weeks=amount)
    months = amount * 12 if unit.startswith("Y") else amount
    year, month = divmod(start.month - 1 + months, 12)
    year += start.year
    return date(year, month + 1, min(start.day, monthrange(year, month + 1)[1]))


def parse_expiry_details(text: str, today: date | None = None) -> dict:
    """Parse Indian package date text into expiry/manufacture dates.

    Dates are labelled by the nearest preceding keyword (EXP/USE BY/BEST BEFORE vs MFG/PKD/PACKED),
    so a manufacture date printed before the expiry date is never mistaken for it. Supports
    "best before N months from manufacture". Ambiguous numeric dates are read as DD/MM/YY; MM/YY
    and MMM YYYY resolve to the last day of that month.
    """
    today = today or date.today()
    normalized = re.sub(r"\s+", " ", text.upper())
    low, high = _add_duration(today, -3, "YEARS"), _add_duration(today, 10, "YEARS")

    taken: list[tuple[int, int]] = []
    candidates: list[dict] = []
    for kind, pattern in _DATE_PATTERNS:
        for match in pattern.finditer(normalized):
            start, end = match.span()
            if any(start < t_end and t_start < end for t_start, t_end in taken):
                continue
            parsed = _to_date(kind, match.groups())
            if parsed is None or not (low <= parsed <= high):
                continue
            taken.append((start, end))
            candidates.append({"date": parsed, "start": start, "label": None, "keyword_start": None})

    keywords = [(m.start(), m.end(), "expiry") for m in _EXPIRY_KW.finditer(normalized)]
    keywords += [(m.start(), m.end(), "mfg") for m in _MFG_KW.finditer(normalized)]
    relative = _RELATIVE.search(normalized)
    for candidate in candidates:
        preceding = [k for k in keywords if k[1] <= candidate["start"] and candidate["start"] - k[1] <= _KEYWORD_WINDOW]
        if preceding:
            k_start, _, label = max(preceding, key=lambda k: k[1])
            # "BEST BEFORE 6 MONTHS FROM 01/10/25": the date belongs to the relative phrase -> manufacture.
            if relative and label == "expiry" and k_start == relative.start():
                label = "mfg"
            candidate["label"], candidate["keyword_start"] = label, k_start

    mfg = sorted(c["date"] for c in candidates if c["label"] == "mfg")
    labelled_expiry = sorted(c["date"] for c in candidates if c["label"] == "expiry")
    unlabelled = sorted(c["date"] for c in candidates if c["label"] is None)
    manufactured = mfg[0] if mfg else None
    result = {"expiry": None, "manufactured": manufactured, "method": None, "candidates": len(candidates)}

    if labelled_expiry:
        result.update(expiry=labelled_expiry[-1], method="labelled_expiry")
    elif relative and manufactured:
        result.update(expiry=_add_duration(manufactured, int(relative.group(1)), relative.group(2)),
                      method="relative_from_manufacture")
    elif len(unlabelled) >= 2:
        result.update(expiry=unlabelled[-1], method="latest_unlabelled")
        if manufactured is None:
            result["manufactured"] = unlabelled[0]
    elif len(unlabelled) == 1:
        result.update(expiry=unlabelled[0], method="single_unlabelled")

    # A chosen expiry that is not after the manufacture date is inconsistent -> unreadable.
    if result["expiry"] and result["manufactured"] and result["expiry"] <= result["manufactured"]:
        result.update(expiry=None, method=None)
    return result


def parse_expiry(text: str, today: date | None = None) -> date | None:
    """Return only the expiry date (see parse_expiry_details)."""
    return parse_expiry_details(text, today=today)["expiry"]


def normalize_category(category: str) -> str:
    return re.sub(r"[\s\-]+", "_", category.strip().lower())


def evaluate_expiry(
    received: date,
    expiry: date | None,
    category: str,
    policy: dict,
    manufactured: date | None = None,
    sku: str | None = None,
) -> dict:
    """Shelf-life policy check.

    Remaining-life % uses this pack's own manufacture->expiry span when known ("pack_label"),
    else a per-SKU reference, else the category reference shelf life.
    """
    category_config = policy.get("categories", {}).get(normalize_category(category))
    sku_config = policy.get("skus", {}).get(str(sku)) if sku else None
    if category_config is None and sku_config is None:
        return {"decision": "review_required", "reason_codes": ["unknown_category"], "remaining_days": None,
                "remaining_life_pct": None, "required_remaining_life_pct": 0.0, "life_basis": None}
    config = {**(category_config or {}), **(sku_config or {})}
    threshold = float(config["min_remaining_life_pct"])
    if expiry is None:
        return {"decision": "review_required", "reason_codes": ["expiry_unreadable"], "remaining_days": None,
                "remaining_life_pct": None, "required_remaining_life_pct": threshold, "life_basis": None}
    days = (expiry - received).days
    if manufactured is not None and expiry > manufactured:
        total_life, basis = (expiry - manufactured).days, "pack_label"
    else:
        total_life = max(1, int(config["reference_shelf_life_days"]))
        basis = "sku_reference" if sku_config and "reference_shelf_life_days" in sku_config else "category_reference"
    pct = round(100.0 * days / total_life, 1)
    if days < 0:
        decision, reason = "reject", "expired_at_receiving"
    elif pct < threshold:
        decision, reason = "hold", "remaining_shelf_life_below_policy"
    else:
        decision, reason = "accept", "shelf_life_policy_pass"
    return {"decision": decision, "reason_codes": [reason], "remaining_days": days, "remaining_life_pct": pct,
            "required_remaining_life_pct": threshold, "life_basis": basis}
