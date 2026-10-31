# Insta-Audit

Insta-Audit is a modular, edge-oriented quality-control system for quick-commerce stores. It combines drop-to-bag event auditing (visual SKU match + measured weight delta), inwarding expiry OCR, produce condition triage, a FastAPI service, and the original browser demo.

The demo runs by opening `index.html`. The ML service is a separate Python application under `src/insta_audit`.

## Project status and production boundary

This repository provides an engineering baseline and data/training workflows. It includes a locally generated 66-SKU DINOv2 reference bank built from public, India-market packaged-grocery catalog photos, but does **not** contain store-trained packing/freshness models, live store footage, or hardware drivers. The barcode-prefix and product-category rules narrow this demo catalog; they do not verify that an item is listed by Instamart. Until store-specific models and sensor adapters are supplied, uncertain inference returns explicit `review_required` results rather than invented confidence scores. Data collection, model validation, sensor calibration, and a controlled store pilot are still needed before operational deployment.

The browser UI is served by FastAPI. Catalog image matching and produce freshness upload call the real model endpoints; packing drop events and expiry OCR remain simulated/manual-entry demo flows. All public-data model results must be presented as prototype triage, not validated Instamart performance.

The suggested public datasets are not interchangeable or automatically production-licensed. Products-10K and RPC are non-commercial research datasets; MVTec D2S has separate access and license terms. They are not silently downloaded or used by the default pipeline. Open Food Facts can provide India-market grocery metadata and reference photos, subject to ODbL / CC BY-SA attribution and share-alike obligations. Its catalog is not verified against Instamart's assortment, and its photos are not a substitute for real packing-camera views. For commercial deployment, use appropriately licensed assets and a store-owned, consented, annotated evaluation set.

## Repository map

- `src/insta_audit/api`: FastAPI health, packing audit, and inwarding endpoints.
- `src/insta_audit/vision`: SKU embedding bank and optional detector adapters.
- `src/insta_audit/quality`: shelf-life policy, expiry OCR adapter, and freshness model adapter.
- `src/insta_audit/sensors`: load-cell interface and calibration utilities.
- `src/insta_audit/data`: Open Food Facts importer and dataset manifest/validation tools.
- `scripts`: catalog ingestion, anchor-bank build, training entry points, and video evaluation.
- `configs`: policy and example SKU catalog.
- `data/README.md`: folder conventions and capture/evaluation protocol.

## Setup

Python 3.11 is recommended. Create a virtual environment, then install:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[api,vision,training]"
```

Optional OCR adapters:

```powershell
pip install -e ".[ocr]"
```

OCR remains unloaded unless `INSTA_AUDIT_ENABLE_OCR=true` and local PaddleOCR detection, recognition, and angle-classifier model directories are configured. Model files must be provisioned and reviewed before deployment.

## Data workflow

1. Review `data/README.md` and the dataset notices in `data/datasets.yaml`.
2. Fetch only a small set of India-tagged products with Indian barcode prefixes and names matched to the demo grocery categories: `python scripts/fetch_open_food_facts.py --limit 100 --download-images --output data/catalog/openfoodfacts_india.jsonl`. This filter runs before image downloads. The current 66-SKU sample is a filtered subset of an earlier 100-row sample; its original metadata is retained in `data/catalog/openfoodfacts_india.raw.jsonl` for provenance. Review each image's attribution/rights. The sample demonstrates catalog lookup only and is not verified Instamart assortment data.
3. Organize the curated photos with `python scripts/prepare_open_food_facts_anchors.py`. This creates a draft SKU manifest and one-view anchor folders; add owned back/side/tilted views and measured unit weights before packing evaluation. The DINOv2 bank in `artifacts/sku_bank.npz` currently contains 66 SKUs, one reference view each.
4. Add owned store-camera frames using the layouts in `data/README.md`; keep order/session IDs grouped when splitting to prevent leakage.
5. Copy `configs/localizer.example.yaml` to a private/local `data/localizer.yaml`, edit it to point to annotated capture data, then train: `python scripts/train_detector.py --data data/localizer.yaml`.
6. Build SKU embeddings from reviewed anchor folders: `python scripts/build_embedding_bank.py --catalog data/catalog/skus.yaml --images data/anchors --output artifacts/sku_bank.npz`.
7. Prepare AgriFreshNET using `python scripts/prepare_agrifreshnet.py`, then train with `python scripts/train_freshness.py --data data/inward/freshness_model_complete --output artifacts/freshness.ts`. The saved checkpoint is selected by rotten-class recall. This public-data model is a transfer baseline, not validation against the target receiving view.
8. For an edge embedding runtime, export a locally provisioned DINOv2 snapshot with `python scripts/export_dino_onnx.py --model-dir models/dinov2-small`. Compile and benchmark the ONNX model for the actual target runtime; no latency claim is assumed here.
9. Start the development service with the real bank/model selected:

   ```powershell
   $env:INSTA_AUDIT_SKU_BANK_PATH = "artifacts/sku_bank.npz"
   $env:INSTA_AUDIT_EMBEDDING_MODEL_PATH = "facebook/dinov2-small"
   $env:INSTA_AUDIT_FRESHNESS_MODEL_PATH = "artifacts/freshness.ts"
   uvicorn insta_audit.api.main:app --host 127.0.0.1 --port 8080
   ```

   Open `http://127.0.0.1:8080/`; **Real Catalog Image Check** uses the DINOv2 bank and the inward **Analyze freshness** control uses the trained classifier. Packing drops and expiry date interpretation are simulated/manual; live camera, OCR, and scale adapters are not connected.

## API

- `GET /health` — liveness and model readiness.
- `POST /v1/packing/audit` — one drop event, expected SKU, visual embedding (or crop reference), and calibrated weight delta.
- `POST /v1/inward/expiry` — optional close-up image or recognized text plus received date and category. Set `INSTA_AUDIT_ENABLE_OCR=true` to load the optional OCR adapter.
- `POST /v1/inward/freshness` — produce image for condition triage.

See `docs/architecture.md` and the OpenAPI page at `/docs` after starting the service. Integrate camera, scale, order service, and secure event transport through store-specific adapters; do not expose the demo server directly to a public network. The Docker image is API-only; install the matching `[vision]` / `[ocr]` extras and mount reviewed local model artifacts to enable those adapters. In production mode, set `INSTA_AUDIT_API_KEY`.
