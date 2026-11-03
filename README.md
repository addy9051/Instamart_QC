# Insta-Audit

Insta-Audit is a modular, edge-oriented quality-control system for quick-commerce stores. It combines drop-to-bag event auditing (visual SKU match + measured weight delta), inwarding expiry OCR, produce condition triage, a FastAPI service, and the original browser demo.

The demo runs by opening `index.html`. The ML service is a separate Python application under `src/insta_audit`.

## Project status and production boundary

This repository provides an engineering baseline and data/training workflows. It includes a locally generated <!--bank:sku_count-->68<!--/bank-->-SKU DINOv2 reference bank (<!--bank:reference_views-->108<!--/bank--> reference views; numbers are synced from the bank metadata by `scripts/sync_readme_stats.py`) built from public, India-market packaged-grocery catalog photos, but does **not** contain store-trained packing/freshness models, live store footage, or hardware drivers. The barcode-prefix and product-category rules narrow this demo catalog; they do not verify that an item is listed by Instamart. Until store-specific models and sensor adapters are supplied, uncertain inference returns explicit `review_required` results rather than invented confidence scores. Data collection, model validation, sensor calibration, and a controlled store pilot are still needed before operational deployment.

The browser UI is served by FastAPI. Catalog image matching and produce freshness upload call the real model endpoints; packing drop events and expiry OCR remain simulated/manual-entry demo flows. All public-data model results must be presented as prototype triage, not validated Instamart performance.

The suggested public datasets are not interchangeable or automatically production-licensed. Products-10K and RPC are non-commercial research datasets; MVTec D2S has separate access and license terms. They are not silently downloaded or used by the default pipeline. Open Food Facts can provide India-market grocery metadata and reference photos, subject to ODbL / CC BY-SA attribution and share-alike obligations. Its catalog is not verified against Instamart's assortment, and its photos are not a substitute for real packing-camera views. For commercial deployment, use appropriately licensed assets and a store-owned, consented, annotated evaluation set.

## Product direction (summary)

The concept has been repositioned from "visual SKU verification of every item" to **proof-of-pack and order-completeness assurance for dark stores**. Full design, formulas, release gates, and pilot plan are in [`docs/product_direction.md`](docs/product_direction.md). I1–I4 and I9 (scan-vs-bag, proof-of-pack, operating modes, seal weight, privacy controls) are implemented as code; I5–I8 are still planned. Section 12 of that document lists the exact status.

| # | Issue in the original idea | Revised approach |
|---|---|---|
| I1 | Visual SKU checks duplicate the picker's barcode scan | Barcode-first: trust the scan for identity and audit it (scanned-not-bagged, bagged-twice, quantity, produce, damage, near-expiry) |
| I2 | Unverifiable "missing item" refund claims ignored | Tamper-evident **proof-of-pack** bundle per bag (drop keyframes, seal frame, seal weight, scan list, hash chain) for CX disputes |
| I3 | Per-item false holds break the 10-minute SLA | `off / shadow / advisory / enforce` modes with kill switch; hold only on high-confidence + high-cost errors; per-*order* false-hold gate (≤0.5–1%) and zero seconds added for correct orders |
| I4 | Per-item weight deltas are unreliable in real packing | One settled **seal-time weighing** per bag against an expected gross band built from measured per-SKU gross mean/std; ranges for variable-weight items |
| I5 | No unit economics | Cost-per-order vs. benefit-per-order model; hardware tiers T0 (scale only) → T1 (+ camera, CPU) → T2 (+ edge GPU), upgrading only on measured value |
| I6 | Static SKU bank can't keep up with catalog churn | Multi-view + weight **enrollment at GRN**, first-party catalog bootstrap, packaging-change detection, lookalike groups, scan + weight fallback for unenrolled SKUs |
| I7 | Generic single-image freshness label ≠ FnV receiving QC | Per-commodity specs, crate sampling, "% defective by type" mapped to accept / discount / reject; current model is triage only |
| I8 | Expiry OCR at GRN duplicates ASN/WMS data | OCR verifies ASN expiry on a sample; new **pick-time near-expiry** check against per-SKU customer minimums; FEFO violation metric |
| I9 | Camera surveillance and DPDP Act 2023 risk | Bag-mouth ROI only, on-edge face/label redaction, purpose limitation (no individual performance scoring), short retention, RBAC, privacy impact assessment before the pilot |

Rollout follows a staged pilot: baseline → shadow T0 → shadow T1 → advisory with a matched control store → selective enforce.

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
2. Fetch only a small set of India-tagged products with Indian barcode prefixes and names matched to the demo grocery categories: `python scripts/fetch_open_food_facts.py --limit 100 --download-images --output data/catalog/openfoodfacts_india.jsonl`. This filter runs before image downloads. The current <!--bank:sku_count-->68<!--/bank-->-SKU sample is a filtered subset of an earlier 100-row sample; its original metadata is retained in `data/catalog/openfoodfacts_india.raw.jsonl` for provenance. Review each image's attribution/rights. The sample demonstrates catalog lookup only and is not verified Instamart assortment data.
3. Organize the curated photos with `python scripts/prepare_open_food_facts_anchors.py`. This creates a draft SKU manifest and one-view anchor folders; add owned back/side/tilted views and measured unit weights before packing evaluation. The DINOv2 bank in `artifacts/sku_bank.npz` currently contains <!--bank:sku_count-->68<!--/bank--> SKUs and <!--bank:reference_views-->108<!--/bank--> reference views.
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

- `GET /health` — liveness, model readiness, embedder/bank compatibility (`status: degraded` on a mismatch) and embedding-failure count.
- `POST /v1/packing/seal` — **order/bag-level audit at sealing** (I1, I3, I4): the order lines and picker scans are looked up server-side by `order_id`; the client reports only the settled seal weight, optional bagged counts, and optional frames. Returns the decision, the operating `mode`, the resulting `action` (`none | notify | block`), and the proof-of-pack head hash when capture is enabled.
- `POST /v1/orders/webhook` — HMAC-signed OMS "order ready to pack" push. Contract: `docs/oms_webhook_contract.md`.
- `GET /v1/proof/{order_id}/{bag_id}` — proof-of-pack manifest plus hash-chain verification (I2).
- `POST /v1/admin/kill-switch`, `POST /v1/admin/proof/purge` — force every store to `off`; delete bundles past retention. Require an API key outside development.
- `POST /v1/packing/audit` — one drop event (per-item path). The client sends only what it measured (`measured_weight_delta_g`, `settled`, embedding or crop, `expected_sku`, `expected_quantity`); weight, tolerance and identity come from the server-side catalog, the unit count is inferred from weight, and the old `expected_unit_weight_g`, `observed_sku`, `observed_quantity` and `max_weight_error_pct` fields are rejected with 422. Matching is top-k with a margin and an `unknown_item` path.
- `POST /v1/inward/expiry` — optional close-up image or recognized text plus received date and category. Set `INSTA_AUDIT_ENABLE_OCR=true` to load the optional OCR adapter.
- `POST /v1/inward/freshness` — produce image for condition triage.

### Operating modes, proof-of-pack and privacy

Every store starts in `shadow` (`configs/modes.yaml`): decisions are logged but never shown to packers. `advisory` notifies; `enforce` can block, but only for confident holds (count and verified-gross weight agree) and only when `INSTA_AUDIT_ALLOW_ENFORCE=true`. SKU overrides can only make a store more lenient. Evaluate on per-order rates with `python scripts/simulate_seal_eval.py` (synthetic design check) and `insta_audit.metrics.order_report` on real shadow data.

Proof-of-pack is off by default (`INSTA_AUDIT_PROOF_ENABLED`). Frames are cropped to the bag-mouth ROI, label zones are blurred, images are downscaled and stripped of metadata, and bundles are deleted after `INSTA_AUDIT_PROOF_RETENTION_DAYS` (call `POST /v1/admin/proof/purge` from a scheduler). Bundles are stored in a local directory or any S3-compatible bucket (`pip install -e ".[s3]"`). Only use it for order-completeness disputes, never to score individual packers, and complete a privacy impact assessment first (`docs/privacy_impact_checklist.md`).

See `docs/product_direction.md`, `docs/architecture.md`, and the OpenAPI page at `/docs` after starting the service. Integrate camera, scale, order service, and secure event transport through store-specific adapters; do not expose the demo server directly to a public network. The Docker image is API-only; install the matching `[vision]` / `[ocr]` extras and mount reviewed local model artifacts to enable those adapters. In production mode, set `INSTA_AUDIT_API_KEY`.
