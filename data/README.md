# Data conventions and capture protocol

Do not commit downloaded datasets or customer/order images. `data/` is git-ignored by convention except for this documentation and the dataset manifest.

## Directory layout

```text
data/
  catalog/                 # Open Food Facts JSONL and local SKU metadata
  anchors/<sku>/            # 4+ owned pack photos per SKU; use front/back/sides/tilted views
  packing/<session_id>/     # short videos or extracted frames, grouped by capture session
  inward/<category>/<class>/# freshness images grouped as fresh / review / spoiled
  annotations/              # COCO/YOLO labels and event-level ground truth
  splits/                   # split manifests grouped by store/day/session
```

## Capture protocol

- Use a fixed, documented camera pose at the bag mouth and retain original timestamps. Capture normal movement, glare, motion blur, face-down packs, creases, hands, bag occlusion, and realistic ambient lighting.
- Record a synchronized scale trace, order line items, expected SKU/quantity, actual inserted SKU/quantity, and event timestamps. Calibrate the scale with known weights before capture and document the calibration version.
- Inwarding images should be close-up package date stamps plus batch metadata. For produce, capture accepted, borderline, and rejected examples under the intended receiving light and packaging. A camera cannot certify hidden defects or food safety.
- Obtain site, staff, and data-governance approval; avoid faces and customer identifiers. Hash or pseudonymize order/session IDs.
- Annotate boxes/SKU/track IDs and per-item timestamps; use two reviewers for high-cost error labels. Keep a review class for ambiguity instead of forcing a SKU label.
- Split by capture session/store/day before augmentation. Never put adjacent frames from one video in both train and test. Reserve a final untouched store/day test set.

## Seal-weight catalog fields

The seal-weight check (`/v1/packing/seal`) uses the SKU weights in `data/catalog/skus.yaml`. A net pack-size weight is **not** a scale reading, so it only gives a wide, low-confidence band that can never produce a hold. To enable confident checks, weigh at least 20 real sealed units per SKU on the station scale and record:

```yaml
- sku: '8901491501054'
  gross_mean_g: 58.4       # mean of the weighings, packaging included
  gross_std_g: 1.2         # sample standard deviation
  gross_verified: true     # set only after the weighings above
  weight_tolerance_pct: 3  # optional per-SKU relative tolerance
# variable-weight items (loose produce) use a range instead:
- sku: tomatoes_500g
  min_weight_g: 450
  max_weight_g: 550
```

## Label formats

- Detector training: Ultralytics YOLO directory format, a `data.yaml` file with explicit class names and `train` / `val` paths.
- Anchor bank: one folder per SKU containing owned reference photos. The SKU folder name must match `configs/skus.yaml`.
- Freshness training: `ImageFolder` layout with matching train/val stages (`fresh` + `spoiled`, or `fresh` + `semi_fresh` + `rotten`). Reorganize source files by their provided stage labels; keep ambiguous cases in a review set instead of forcing a class.
- Packing audit evaluation: JSONL with `session_id`, `event_id`, `expected_sku`, `observed_sku`, `expected_quantity`, `observed_quantity`, `expected_weight_g`, `measured_weight_delta_g`, and ground-truth disposition.

## Public data that can start the model work

- **Detector warm start:** the Humans in the Loop Supermarket Shelves sample has 45 real shelf images and 11,743 product/price boxes. The publisher says it is CC0 and permits commercial use. It can warm-start product localization, but it is not packing-bag footage and includes price tags as a second class.
- **Freshness warm start:** AgriFreshNET has 14,500 real-world produce photos from markets, supermarkets, homes, farms, and storage settings, with fresh / semi-fresh / rotten labels and a CC BY 4.0 license. It now fits the three-grade freshness training script. It does not establish accuracy on produce behind Instamart packaging or under receiving-dock lighting.
- **SKU references (outward packing QC):** the importer filters to India-tagged products with 890 barcode prefixes and names matching packaged grocery categories (snacks, biscuits, dairy, pantry staples, condiments, and beverages) before downloading images. This is a practical scope heuristic for an Indian quick-commerce demo, not proof of Instamart assortment membership or country of manufacture. The images initialize SKU embeddings only. Use the recorded attribution and keep per-image rights in mind.
- **Inward produce QC:** use a produce freshness dataset such as AgriFreshNET for a labeled transfer-learning baseline. It does not represent dark-store receiving conditions, packed goods, expiry stamps, or actual Instamart supply. Validate and calibrate any triage model on store data before operational use.
- **Packing event stream:** these sources do not provide synchronized order line items, bag-mouth videos, counts, and scale deltas for quick-commerce packing. Use public shelf data for detector warm-start and a clearly labeled staged drop-to-bag demonstration for event logic. A customer pilot still needs a held-out store capture set.

The previously suggested Fruits Fresh/Rotten Kaggle dataset currently shows an unknown license, so do not include it in a client package without permission. The benchmark sources in `datasets.yaml` also have their own non-commercial restrictions. Review each record and current source terms before use.
