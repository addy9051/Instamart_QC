import argparse
import json
import shutil
from pathlib import Path

from insta_audit.data.open_food_facts import instamart_demo_category


def main() -> None:
    parser = argparse.ArgumentParser(description="Keep only India-market packaged grocery references useful for an Instamart QC prototype")
    parser.add_argument("--catalog", type=Path, default=Path("data/catalog/openfoodfacts_india.jsonl"))
    parser.add_argument("--image-root", type=Path, default=Path("data/catalog/images"))
    parser.add_argument("--preserve-raw", action="store_true", help="copy the unfiltered API sample next to the curated catalog")
    args = parser.parse_args()

    rows = [json.loads(line) for line in args.catalog.read_text(encoding="utf-8").splitlines() if line.strip()]
    image_root = args.image_root.resolve()
    if args.preserve_raw:
        raw_path = args.catalog.with_name(args.catalog.stem + ".raw" + args.catalog.suffix)
        if not raw_path.exists():
            shutil.copy2(args.catalog, raw_path)

    curated = []
    seen = set()
    for row in rows:
        sku = str(row.get("sku", "")).strip()
        tags = row.get("countries_tags") or []
        if not sku.startswith("890") or "en:india" not in tags or sku in seen:
            continue
        category = instamart_demo_category(str(row.get("name", "")), str(row.get("brand", "")))
        if not category:
            continue
        image = row.get("downloaded_image")
        if not image:
            continue
        image_path = Path(image).resolve()
        if not image_path.is_relative_to(image_root) or not image_path.is_file():
            continue
        seen.add(sku)
        curated.append({**row, "instamart_demo_category": category})

    args.catalog.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in curated), encoding="utf-8")
    counts: dict[str, int] = {}
    for row in curated:
        category = row["instamart_demo_category"]
        counts[category] = counts.get(category, 0) + 1
    print(f"Curated {len(curated)} image-backed India-market grocery SKUs from {len(rows)} rows.")
    for category, count in sorted(counts.items()):
        print(f"  {category}: {count}")


if __name__ == "__main__":
    main()
