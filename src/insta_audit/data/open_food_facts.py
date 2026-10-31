import json
import hashlib
import os
import time
from urllib.parse import urlparse
from pathlib import Path

import requests

API_URL = "https://world.openfoodfacts.org/cgi/search.pl"
FIELDS = "code,product_name,brands,quantity,countries_tags,image_front_url,image_url,url"


def fetch_india_catalog(
    limit: int,
    output: Path,
    user_agent: str,
    page_size: int = 100,
    image_dir: Path | None = None,
) -> int:
    """Fetch India-tagged catalog metadata; image files remain opt-in and separately licensed."""
    if limit < 1 or limit > 20_000:
        raise ValueError("limit must be between 1 and 20000")
    output.parent.mkdir(parents=True, exist_ok=True)
    if image_dir:
        image_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent, "Accept": "application/json"})
    written, page = 0, 1
    temporary_output = output.with_suffix(output.suffix + ".tmp")
    with temporary_output.open("w", encoding="utf-8") as stream:
        while written < limit:
            params = {
                "action": "process", "json": 1, "page": page,
                "page_size": min(page_size, limit - written), "fields": FIELDS,
                "tagtype_0": "countries", "tag_contains_0": "contains", "tag_0": "en:india",
            }
            response = None
            for attempt in range(4):
                try:
                    response = session.get(API_URL, params=params, timeout=(10, 45))
                    response.raise_for_status()
                    break
                except requests.RequestException:
                    if attempt == 3:
                        raise
                    time.sleep(2**attempt)
            products = response.json().get("products", [])
            if not products:
                break
            for product in products:
                code = str(product.get("code", "")).strip()
                name = str(product.get("product_name", "")).strip()
                if not code or not name:
                    continue
                row = {
                    "sku": code,
                    "name": name,
                    "brand": product.get("brands", ""),
                    "quantity": product.get("quantity", ""),
                    "countries_tags": product.get("countries_tags", []),
                    "image_url": product.get("image_front_url") or product.get("image_url"),
                    "source": "Open Food Facts",
                    "source_url": product.get("url"),
                    "database_license": "ODbL",
                    "image_license": "CC BY-SA; check image-specific rights/attribution",
                }
                if image_dir and row["image_url"]:
                    image_path: Path | None = None
                    try:
                        image_response = session.get(row["image_url"], timeout=(10, 30), stream=True)
                        image_response.raise_for_status()
                        content_type = image_response.headers.get("Content-Type", "")
                        if content_type.startswith("image/"):
                            image_path = image_dir / f"{hashlib.sha256(code.encode()).hexdigest()}.jpg"
                            written_bytes = 0
                            with image_path.open("wb") as image_file:
                                for chunk in image_response.iter_content(64 * 1024):
                                    written_bytes += len(chunk)
                                    if written_bytes > 10_000_000:
                                        raise ValueError("image exceeds 10 MB limit")
                                    image_file.write(chunk)
                            row["downloaded_image"] = str(image_path)
                        time.sleep(0.25)
                    except (requests.RequestException, OSError, ValueError):
                        if image_path:
                            image_path.unlink(missing_ok=True)
                        row["downloaded_image"] = None
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                written += 1
                if written >= limit:
                    break
            page += 1
            time.sleep(1.0)  # Be polite to the public API.
    os.replace(temporary_output, output)
    return written


def download_catalog_images(catalog_path: Path, image_dir: Path, user_agent: str) -> int:
    """Download linked OFF front photos from an existing JSONL metadata export."""
    image_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent, "Accept": "image/*"})
    records = [json.loads(line) for line in catalog_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    downloaded = 0
    for record in records:
        url = record.get("image_url")
        if not url:
            record["downloaded_image"] = None
            continue
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "images.openfoodfacts.org":
            record["downloaded_image"] = None
            continue
        image_path = image_dir / f"{hashlib.sha256(str(record['sku']).encode()).hexdigest()}.jpg"
        try:
            written_bytes = 0
            with session.get(url, timeout=(10, 30), stream=True) as response:
                response.raise_for_status()
                if not response.headers.get("Content-Type", "").startswith("image/"):
                    raise ValueError("response is not an image")
                with image_path.open("wb") as image_file:
                    for chunk in response.iter_content(64 * 1024):
                        written_bytes += len(chunk)
                        if written_bytes > 10_000_000:
                            raise ValueError("image exceeds 10 MB limit")
                        image_file.write(chunk)
            record["downloaded_image"] = str(image_path)
            downloaded += 1
        except (requests.RequestException, OSError, ValueError):
            image_path.unlink(missing_ok=True)
            record["downloaded_image"] = None
        time.sleep(0.25)
    catalog_path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8")
    return downloaded
