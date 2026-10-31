import argparse
from pathlib import Path

from insta_audit.data.open_food_facts import download_catalog_images


def main() -> None:
    parser = argparse.ArgumentParser(description="Download image URLs from an Open Food Facts JSONL export")
    parser.add_argument("--catalog", type=Path, default=Path("data/catalog/openfoodfacts_india.jsonl"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/catalog/images"))
    parser.add_argument("--user-agent", default="InstaAuditPrototype/0.1 (catalog-image-ingestion)")
    args = parser.parse_args()
    count = download_catalog_images(args.catalog, args.output_dir, args.user_agent)
    print(f"Downloaded {count} product images to {args.output_dir}; attribution metadata remains in {args.catalog}.")


if __name__ == "__main__":
    main()
