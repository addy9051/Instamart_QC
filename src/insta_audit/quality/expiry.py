import re
from calendar import monthrange
from datetime import date

MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def parse_expiry(text: str, today: date | None = None) -> date | None:
    """Parse common Indian package date strings; ambiguous numeric dates are DD/MM/YY."""
    normalized = re.sub(r"\s+", " ", text.upper().replace("USE BY", " ").replace("EXP", " "))
    patterns = [
        (r"\b(\d{1,2})[ /.-]([A-Z]{3})[ ,.-]+(\d{2,4})\b", "dmy_word"),
        (r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\b", "dmy_num"),
        (r"\b(\d{4})[/.\-](\d{1,2})[/.\-](\d{1,2})\b", "ymd"),
        (r"\b(\d{1,2})[/.\-](\d{4})\b", "my"),
    ]
    today = today or date.today()
    for pattern, kind in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        try:
            if kind == "dmy_word":
                day, month, year = int(match.group(1)), MONTHS[match.group(2)], int(match.group(3))
            elif kind == "dmy_num":
                day, month, year = map(int, match.groups())
            elif kind == "ymd":
                year, month, day = map(int, match.groups())
            else:
                month, year = map(int, match.groups())
                day = monthrange(year, month)[1]
            if year < 100:
                year += 2000 if year < 70 else 1900
            parsed = date(year, month, day)
            return parsed if parsed >= today.replace(year=today.year - 3) else None
        except (ValueError, KeyError, OverflowError):
            continue
    return None


def evaluate_expiry(
    received: date,
    expiry: date | None,
    category: str,
    policy: dict,
) -> dict:
    categories = policy.get("categories", {})
    config = categories.get(category.lower())
    if config is None:
        return {"decision": "review_required", "reason_codes": ["unknown_category"], "remaining_days": None, "remaining_life_pct": None, "required_remaining_life_pct": 0.0}
    threshold = float(config["min_remaining_life_pct"])
    if expiry is None:
        return {"decision": "review_required", "reason_codes": ["expiry_unreadable"], "remaining_days": None, "remaining_life_pct": None, "required_remaining_life_pct": threshold}
    days = (expiry - received).days
    total_life = max(1, int(config["reference_shelf_life_days"]))
    pct = round(100.0 * days / total_life, 1)
    if days < 0:
        decision, reason = "reject", "expired_at_receiving"
    elif pct < threshold:
        decision, reason = "hold", "remaining_shelf_life_below_policy"
    else:
        decision, reason = "accept", "shelf_life_policy_pass"
    return {"decision": decision, "reason_codes": [reason], "remaining_days": days, "remaining_life_pct": pct, "required_remaining_life_pct": threshold}
