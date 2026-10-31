import argparse
from pathlib import Path

from insta_audit.data.open_food_facts import fetch_india_catalog


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch a small Open Food Facts India catalog sample")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--output", type=Path, default=Path("data/catalog/openfoodfacts_india.jsonl"))
    parser.add_argument("--user-agent", default="InstaAuditPrototype/0.1 (dataset-ingestion)")
    parser.add_argument("--download-images", action="store_true", help="also fetch catalog photos; review attribution/rights")
    args = parser.parse_args()
    image_dir = Path("data/catalog/images") if args.download_images else None
    count = fetch_india_catalog(args.limit, args.output, args.user_agent, image_dir=image_dir)
    print(f"Wrote {count} records to {args.output}. Review attribution and image rights before reuse.")


if __name__ == "__main__":
    main()
