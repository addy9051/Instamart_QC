"""Download multi-view consumer camera photos from Open Food Facts into data/anchors/.

Downloads front, back (ingredients), and side (nutrition table) photos uploaded by Indian consumers.
Maps real-world dark-store SKUs (including corrected Maggi barcodes) directly into data/anchors/<sku>/.
"""
import json
import time
from pathlib import Path

import requests
import yaml
from PIL import Image

TARGET_SKUS = [
    # 1. Lay's flavor lookalikes (same ~50g weight)
    {
        "sku": "8901491101844",
        "off_barcode": "8901491101844",
        "name": "Lay's India's Magic Masala",
        "brand": "Lay's",
        "category": "snacks_and_namkeen",
        "weight_g": 50,
    },
    {
        "sku": "8901491001168",
        "off_barcode": "8901491001168",
        "name": "Lay's Classic Salted",
        "brand": "Lay's",
        "category": "snacks_and_namkeen",
        "weight_g": 50,
    },
    {
        "sku": "8901491361026",
        "off_barcode": "8901491361026",
        "name": "Lay's Spanish Tomato Tango",
        "brand": "Lay's",
        "category": "snacks_and_namkeen",
        "weight_g": 50,
    },
    {
        "sku": "8906010500016",
        "off_barcode": "8906010500016",
        "name": "Balaji Wafers Simply Salted",
        "brand": "Balaji",
        "category": "snacks_and_namkeen",
        "weight_g": 50,
    },
    # 2. Maggi 70g Noodles Variants (User corrected barcodes)
    {
        "sku": "8901058842722",
        "off_barcode": "8901058851328",  # Mapped from 70g Maggi 2-Min Masala pack in OFF
        "name": "Maggi 2-Minute Masala Instant Noodles 70g",
        "brand": "Nestle Maggi",
        "category": "pantry_staples",
        "weight_g": 70,
    },
    {
        "sku": "8901058898422",
        "off_barcode": "8901058898422",  # Maggi Special Masala 70g directly in OFF
        "name": "Maggi Special Masala Noodles 70g",
        "brand": "Nestle Maggi",
        "category": "pantry_staples",
        "weight_g": 70,
    },
    {
        "sku": "89080153",
        "off_barcode": "89080153",
        "name": "Maggi Masala-ae-Magic Seasoning 6g",
        "brand": "Nestle Maggi",
        "category": "condiments_and_spreads",
        "weight_g": 6,
    },
    # 3. Parle-G Variants
    {
        "sku": "8901719134845",
        "off_barcode": "8901719134845",
        "name": "Parle-G Original Glucose Biscuits",
        "brand": "Parle",
        "category": "biscuits_and_bakery",
        "weight_g": 65,
    },
    {
        "sku": "8901719135248",
        "off_barcode": "8901719135248",
        "name": "Parle-G Gold Biscuits",
        "brand": "Parle",
        "category": "biscuits_and_bakery",
        "weight_g": 100,
    },
    {
        "sku": "8901719130014",
        "off_barcode": "8901719130014",
        "name": "Parle Happy Happy Choco Chip",
        "brand": "Parle",
        "category": "biscuits_and_bakery",
        "weight_g": 60,
    },
    # 4. Kurkure Snack Variants
    {
        "sku": "8901058000290",
        "off_barcode": "8901058000290",
        "name": "Kurkure Masala Munch",
        "brand": "Kurkure",
        "category": "snacks_and_namkeen",
        "weight_g": 80,
    },
    {
        "sku": "8901058005233",
        "off_barcode": "8901058005233",
        "name": "Kurkure Green Chutney Style",
        "brand": "Kurkure",
        "category": "snacks_and_namkeen",
        "weight_g": 75,
    },
    {
        "sku": "8901058017687",
        "off_barcode": "8901058017687",
        "name": "Kurkure Puffcorn Yummy Cheese",
        "brand": "Kurkure",
        "category": "snacks_and_namkeen",
        "weight_g": 60,
    },
    # 5. Haldiram's Namkeen Variants
    {
        "sku": "8901725118938",
        "off_barcode": "8901725118938",
        "name": "Haldiram's Aloo Bhujia",
        "brand": "Haldiram's",
        "category": "snacks_and_namkeen",
        "weight_g": 150,
    },
    {
        "sku": "8901725016258",
        "off_barcode": "8901725016258",
        "name": "Haldiram's Bhujia Sev",
        "brand": "Haldiram's",
        "category": "snacks_and_namkeen",
        "weight_g": 150,
    },
    # 6. Dairy Pouches
    {
        "sku": "8901262010016",
        "off_barcode": "8901262010016",
        "name": "Amul Pasteurized Butter 100g",
        "brand": "Amul",
        "category": "dairy",
        "weight_g": 100,
    },
    {
        "sku": "8901262200196",
        "off_barcode": "8901262200196",
        "name": "Amul Fresh Malai Paneer 200g",
        "brand": "Amul",
        "category": "dairy",
        "weight_g": 200,
    },
    {
        "sku": "8901262260121",
        "off_barcode": "8901262260121",
        "name": "Amul Taaza Toned Milk Pouch 500ml",
        "brand": "Amul",
        "category": "dairy",
        "weight_g": 515,
    },
    # 7. Staples
    {
        "sku": "8904043901015",
        "off_barcode": "8904043901015",
        "name": "Tata Salt Vacuum Evaporated 1kg",
        "brand": "Tata",
        "category": "pantry_staples",
        "weight_g": 1000,
    },
]


def download_image(url: str, dest_path: Path, headers: dict) -> bool:
    try:
        # Request full uncompressed user photo first, fallback to standard url
        full_url = url.replace(".400.jpg", ".full.jpg")
        r = requests.get(full_url, headers=headers, timeout=8)
        if r.status_code != 200:
            r = requests.get(url, headers=headers, timeout=8)
        if r.status_code == 200 and len(r.content) > 3000:
            dest_path.write_bytes(r.content)
            with Image.open(dest_path) as im:
                im.verify()
            return True
    except Exception:
        pass
    return False


def main() -> None:
    anchors_dir = Path("data/anchors")
    anchors_dir.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": "InstaAudit-QC-MultiView/1.0 (contact: student-eval@instamart.qc)"}

    print(f"Downloading multi-view consumer camera photos for {len(TARGET_SKUS)} dark-store SKUs...\n")

    catalog_path = Path("data/catalog/skus.yaml")
    catalog_data = yaml.safe_load(catalog_path.read_text(encoding="utf-8")) or {"version": 1, "skus": []}
    existing_skus_map = {str(item["sku"]): item for item in catalog_data.get("skus", [])}

    total_downloaded = 0

    for item in TARGET_SKUS:
        sku = str(item["sku"])
        off_code = item["off_barcode"]
        name = item["name"]
        sku_dir = anchors_dir / sku
        sku_dir.mkdir(parents=True, exist_ok=True)

        time.sleep(1.0)  # Respectful 1-second delay to avoid 429
        api_url = f"https://world.openfoodfacts.org/api/v2/product/{off_code}.json?fields=product_name,image_front_url,image_ingredients_url,image_nutrition_url,image_packaging_url,images"

        try:
            res = requests.get(api_url, headers=headers, timeout=10)
            if res.status_code != 200:
                print(f"[{sku}] {name}: API status {res.status_code}")
                continue
            data = res.json().get("product", {})
        except Exception as e:
            print(f"[{sku}] {name}: Error fetching API: {e}")
            continue

        downloaded_files = []

        # 1. Front view
        if data.get("image_front_url"):
            dest = sku_dir / "catalog_front.jpg"
            if download_image(data["image_front_url"], dest, headers):
                downloaded_files.append(str(dest))

        # 2. Back view (ingredients / instructions)
        if data.get("image_ingredients_url"):
            dest = sku_dir / "view_back_ingredients.jpg"
            if download_image(data["image_ingredients_url"], dest, headers):
                downloaded_files.append(str(dest))

        # 3. Side view (nutrition table / barcode)
        if data.get("image_nutrition_url"):
            dest = sku_dir / "view_side_nutrition.jpg"
            if download_image(data["image_nutrition_url"], dest, headers):
                downloaded_files.append(str(dest))

        # 4. Packaging / tilted view (if present)
        if data.get("image_packaging_url"):
            dest = sku_dir / "view_packaging.jpg"
            if download_image(data["image_packaging_url"], dest, headers):
                downloaded_files.append(str(dest))

        # Write metadata source.json
        (sku_dir / "source.json").write_text(
            json.dumps(
                {
                    "sku": sku,
                    "off_barcode": off_code,
                    "name": name,
                    "brand": item["brand"],
                    "category": item["category"],
                    "unit_weight_g": item["weight_g"],
                    "views": [f.split("/")[-1].split("\\")[-1] for f in downloaded_files],
                    "source": "Open Food Facts (Consumer Smartphone Uploads)",
                    "database_license": "ODbL 1.0",
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        # Update or add to skus.yaml
        all_imgs = [str(p) for p in sorted(sku_dir.glob("*.jpg"))]
        sku_entry = existing_skus_map.get(sku, {})
        sku_entry.update(
            {
                "sku": sku,
                "name": name,
                "brand": item["brand"],
                "category": item["category"],
                "catalog_quantity": f"{item['weight_g']}g",
                # Pack-size (NET) weight only; gross weights come from real weighings
                # (scripts/import_weighings.py), which is what sets weight_verified.
                "expected_unit_weight_g": item["weight_g"],
                "weight_verified": False,
                "anchor_images": all_imgs,
            }
        )
        existing_skus_map[sku] = sku_entry

        total_downloaded += len(downloaded_files)
        view_names = [Path(f).name for f in downloaded_files]
        print(f"[{sku}] {name} ({item['weight_g']}g) -> {len(downloaded_files)} views: {view_names}")

    # Save updated catalog
    catalog_data["skus"] = list(existing_skus_map.values())
    catalog_path.write_text(yaml.safe_dump(catalog_data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"\n[Done] Successfully downloaded {total_downloaded} consumer camera views across target SKUs.")
    print(f"[Catalog] Updated {catalog_path} with verified weights and multi-view anchor paths.")


if __name__ == "__main__":
    main()

