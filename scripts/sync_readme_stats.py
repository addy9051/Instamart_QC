"""Keep README numbers in sync with the SKU bank metadata (C25).

README values live in <!--bank:KEY-->value<!--/bank--> spans. Run without flags to rewrite them from
artifacts/sku_bank.npz.metadata.json, or with --check (CI) to fail when they are stale.
"""
import argparse
import json
import re
import sys
from pathlib import Path

SPAN = re.compile(r"<!--bank:(\w+)-->(.*?)<!--/bank-->", re.S)


def sync(readme: str, metadata: dict) -> tuple[str, list[str]]:
    stale: list[str] = []

    def replace(match: re.Match) -> str:
        key, current = match.group(1), match.group(2)
        if key not in metadata:
            raise KeyError(f"README references unknown bank metadata key {key!r}")
        value = str(metadata[key])
        if value != current:
            stale.append(f"{key}: README has {current!r}, metadata has {value!r}")
        return f"<!--bank:{key}-->{value}<!--/bank-->"

    return SPAN.sub(replace, readme), stale


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
    parser.add_argument("--metadata", type=Path, default=Path("artifacts/sku_bank.npz.metadata.json"))
    parser.add_argument("--check", action="store_true", help="exit 1 if README numbers are stale")
    args = parser.parse_args()
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    original = args.readme.read_text(encoding="utf-8")
    updated, stale = sync(original, metadata)
    if args.check:
        if stale:
            print("README is out of date:\n  " + "\n  ".join(stale), file=sys.stderr)
            raise SystemExit(1)
        print("README numbers match the bank metadata")
        return
    if updated != original:
        args.readme.write_text(updated, encoding="utf-8")
    print(f"Updated {len(stale)} value(s)" if stale else "README already up to date")


if __name__ == "__main__":
    main()
