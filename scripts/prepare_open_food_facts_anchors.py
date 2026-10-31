import argparse
import json
import re
import shutil
from pathlib import Path

import yaml


def main() -> None:
    parser = argparse.ArgumentParser(description="Organize downloaded OFF front photos as initial single-view anchors")
    parser.add_argument("--catalog", type=Path, default=Path("data/catalog/openfoodfacts_india.jsonl"))
    parser.add_argument("--image-root", type=Path, default=Path("data/catalog/images"))
    parser.add_argument("--anchor-root", type=Path, default=Path("data/anchors"))
    parser.add_argument("--manifest", type=Path, default=Path("data/catalog/skus.yaml"))
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.catalog.read_text(encoding="utf-8").splitlines() if line.strip()]
    image_root = args.image_root.resolve()
    args.anchor_root.mkdir(parents=True, exist_ok=True)
    accepted = []
    seen_skus = set()
    for row in rows:
        sku = str(row.get("sku", ""))
        image = row.get("downloaded_image")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", sku) or sku in seen_skus or not image:
            continue
        seen_skus.add(sku)
        source = Path(image).resolve()
        if not source.is_relative_to(image_root) or not source.is_file():
            continue
        sku_dir = args.anchor_root / sku
        sku_dir.mkdir(parents=True, exist_ok=True)
        target = sku_dir / "catalog_front.jpg"
        shutil.copyfile(source, target)
        (sku_dir / "source.json").write_text(json.dumps({
            "source": row.get("source"), "source_url": row.get("source_url"),
            "image_url": row.get("image_url"), "database_license": row.get("database_license"),
            "image_license": row.get("image_license"),
        }, indent=2), encoding="utf-8")
        accepted.append({
            "sku": sku, "name": row.get("name", ""), "brand": row.get("brand", ""),
            "catalog_quantity": row.get("quantity", ""), "expected_unit_weight_g": None,
            "weight_verified": False, "category": "packaged", "anchor_images": [str(target)],
        })
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(yaml.safe_dump({"version": 1, "skus": accepted}, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"Prepared {len(accepted)} single-view anchors. Add owned views and measured unit weights before operational use.")


if __name__ == "__main__":
    main()
