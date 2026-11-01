# Real-data prototype walkthrough

Order: freshness benchmark → catalog lookup → packing audit (workflow design) → expiry policy demo.
Everything below is public-data prototype behaviour. None of it is Instamart accuracy.

## Commands to run yourself (long-running)

Run from the repo root in PowerShell. Paste the output back for each step.

```powershell
cd d:\Downloads\projects\Instamart_QC
.\.venv\Scripts\Activate.ps1
$env:HF_HUB_OFFLINE = 1
```

### 1. Build a leak-free train/val/test split (about 10 s dry run, a few min to copy 14k images)
Conflicting-label groups are excluded from all splits. Fold 0 becomes the untouched test set.
```powershell
python scripts/prepare_agrifreshnet.py --dry-run
# expect: train=8421, val=2808, test=2808, excluded=123; conflicting groups=29
python scripts/prepare_agrifreshnet.py --output data/inward/freshness_model_v3 --manifest data/inward/agrifreshnet_split_manifest_v3.csv
```

### 2. Retrain on the new split with GPU & Early Stopping (~5–10 min on GTX 1650 vs 60+ min CPU)
Trains with mixed precision (AMP) on your local NVIDIA GPU, with early stopping (default 3 epochs patience) tracking spoiled recall:
```powershell
python scripts/train_freshness.py --data data/inward/freshness_model_v3 --output artifacts/freshness_v3.ts --device cuda --epochs 15 --batch-size 32 --patience 3 --workers 0
```
- It explicitly logs: `[Hardware] Training on GPU: NVIDIA GeForce GTX 1650 (4096 MB VRAM)`.
- If validation `spoiled_recall` plateaus for 3 epochs, training stops early and automatically preserves the best checkpoint.
- The script scores the untouched `test/` split once at the end and prints `TEST (untouched split): {...}`. Checkpoint and metadata are saved to `artifacts/freshness_v3.ts`.

### 3. Held-out benchmark and gallery (about 1–3 min on CPU, 2,808 images)
The first line printed is the conflict audit. Expect `conflicting-label groups: 29`.
```powershell
python scripts/eval_freshness_heldout.py --manifest data/inward/agrifreshnet_split_manifest_v3.csv --images data/inward/freshness_model_v3 --model artifacts/freshness_v3.ts
start data\inward\report\freshness_gallery.html
```
Headline numbers come from `excluding_conflicting_groups` in `data/inward/report/freshness_metrics.json`.

If you adopt the new model, copy it over the served one:
```powershell
Copy-Item artifacts\freshness_v3.ts artifacts\freshness.ts -Force
Copy-Item artifacts\freshness_v3.ts.metadata.json artifacts\freshness.ts.metadata.json -Force
```

Skip steps 1–3 and you can still run the old model on its old validation split. This number is optimistic, because that split picked the checkpoint, and the script labels it that way:
```powershell
python scripts/eval_freshness_heldout.py --split val
```

### 4. Catalog lookup (about 1 min, 66 images)
This is a self-match, so it demonstrates the pipeline only. The script prints that caveat. For a real signal, photograph packs from new angles, save them as `<barcode>_front2.jpg` in a folder, and run:
```powershell
python scripts/demo_catalog_lookup.py
python scripts/demo_catalog_lookup.py --query-dir path\to\new_views --output data\catalog\lookup_new_views.csv
```

### 5. Start the demo UI (daemon, leave running)
```powershell
$env:INSTA_AUDIT_SKU_BANK_PATH="artifacts/sku_bank.npz"
$env:INSTA_AUDIT_EMBEDDING_MODEL_PATH="facebook/dinov2-small"
$env:INSTA_AUDIT_FRESHNESS_MODEL_PATH="artifacts/freshness.ts"
uvicorn insta_audit.api.main:app --host 127.0.0.1 --port 8080
```
Open http://127.0.0.1:8080/.

### 6. Optional: rebuild the SKU bank (about 2 min)
```powershell
python scripts/build_embedding_bank.py --catalog data/catalog/skus.yaml --images data/anchors --output artifacts/sku_bank.npz
```

## What the packing page does now
Every button press sends one real request to `/v1/packing/audit`. The decision, reason codes, weight error and similarity on screen are the API's response. Nothing on the page is invented.
- Inputs are expected SKU, observed SKU (typed in by the scenario), quantity and weight (typed in, no scale).
- With no photo the vision step does not run, so a correct item returns `review_required` (`visual_similarity_unavailable`), not `pass`. This is intended.
- Attach an item photo and the DINOv2 match runs once for that audit. It then overrides the typed observed SKU.
- The counters are session-only (items audited, holds, needs review) and start at 0. The round-trip time is browser → local API on CPU, not edge latency.

## Script (what to say and show)

| Step | Show | Say |
|---|---|---|
| 1. Inward produce | Gallery page, then per-class table | "Public AgriFreshNET benchmark on a held-out test split. 29 filename groups appeared under conflicting labels, so they are excluded from train, val and test." |
| 2. Catalog lookup | UI "Single-image catalog lookup", upload an Open Food Facts image | "One reference image per SKU. Matching that image back is a pipeline demo, not recognition of a new view." |
| 3. Packing audit | Packing page, press a scenario with and without a photo | "This is the real audit decision logic on manually supplied inputs. No camera stream exists yet, and continuous tracking needs packing video. Until then the vision step runs once per submitted item photo." |
| 4. Expiry | Inward page | "Policy decision from a manually entered date. It is not OCR." |

## Do not claim
- Instamart accuracy or refund reduction.
- Live detection, a connected camera or scale, or OCR.
- Generalisation to new camera views (needs multi-view anchors and real captures).
