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

## Label formats

- Detector training: Ultralytics YOLO directory format, a `data.yaml` file with explicit class names and `train` / `val` paths.
- Anchor bank: one folder per SKU containing owned reference photos. The SKU folder name must match `configs/skus.yaml`.
- Freshness training: `ImageFolder` layout (`train/{fresh,spoiled}`, `val/{fresh,spoiled}`), with ambiguous cases excluded or separately reviewed.
- Packing audit evaluation: JSONL with `session_id`, `event_id`, `expected_sku`, `observed_sku`, `expected_quantity`, `observed_quantity`, `expected_weight_g`, `measured_weight_delta_g`, and ground-truth disposition.

Public benchmark datasets can help test components, but do not let a benchmark result stand in for held-out India/store-camera evaluation. Review `datasets.yaml` and the source license before each use.
