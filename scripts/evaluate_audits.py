import argparse
import json
from collections import Counter
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize decision quality from paired audit JSONL rows")
    parser.add_argument("--input", type=Path, required=True, help="JSONL rows with truth and prediction fields")
    args = parser.parse_args()
    confusion: Counter[tuple[str, str]] = Counter()
    reasons: Counter[str] = Counter()
    total = 0
    with args.input.open(encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            truth, pred = row["ground_truth"], row["prediction"]
            if truth not in {"pass", "hold"} or pred not in {"pass", "hold", "review_required"}:
                raise ValueError("ground_truth must be pass/hold; prediction pass/hold/review_required")
            confusion[(truth, pred)] += 1
            reasons.update(row.get("reason_codes", []))
            total += 1
    if not total:
        raise SystemExit("no rows found")
    caught = confusion[("hold", "hold")]
    misses = confusion[("hold", "pass")]
    false_holds = confusion[("pass", "hold")]
    print(f"events={total} holds_caught={caught} error_misses={misses} false_holds={false_holds} review={sum(v for (t,p),v in confusion.items() if p=='review_required')}")
    print(f"hold_recall={caught / max(1, caught + misses):.4f}  false_hold_rate={false_holds / max(1, confusion[('pass','pass')] + false_holds):.4f}")
    print("reason_codes=" + json.dumps(dict(reasons), sort_keys=True))


if __name__ == "__main__":
    main()
