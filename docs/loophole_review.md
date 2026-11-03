# Insta-Audit: Loophole review and fixes

Scope: the project idea, the code (`src/`, `scripts/`, configs, Docker), how well it generalises to production, and whether Instamart or another quick-commerce operator would accept it.
Severity: 🔴 blocker · 🟠 major · 🟡 minor.

---

## 1. Project idea

| # | Loophole | Sev | Fix |
|---|---|---|---|
| I1 | **Visual SKU checks repeat the barcode scan.** Instamart pickers already scan each item's barcode on a handheld during picking, so for packaged goods "is this the right SKU?" is mostly answered already. The project doesn't show what it adds beyond the scan. | 🔴 | Go after what the scan misses: **(a)** item scanned but never bagged, or bagged twice; **(b)** wrong quantity; **(c)** produce and loose items with no barcode; **(d)** damaged or leaking packs; **(e)** near-expiry items picked (a FEFO violation). Present vision as a check on the scan, not a replacement for it. |
| I2 | **Fraudulent "missing item" refund claims get no attention.** A large share of refund losses in quick commerce comes from claims that can't be disproved. A per-item hold doesn't help with those. | 🟠 | Add a **proof-of-pack** output: a short clip or keyframes of the bag mouth plus the final sealed weight, keyed to `order_id`. Support/CX can use it to settle disputes. This alone may justify the hardware. |
| I3 | **The 10-minute SLA can't absorb false holds.** The synthetic eval's false-hold rate is **14.8% per item**. For an 8-item order, the chance of at least one false hold is \(1-0.852^8 \approx 72\%\). Any rate like that gets the system switched off on day one. | 🔴 | Make the release gate **per-order** false holds, ≤0.5–1%. Default to **advisory mode**: flag, don't block. Hard holds only for high-confidence plus high-cost errors. Measure packer seconds added per order. |
| I4 | **Packers don't drop one item at a time onto a scale.** In real dark stores, several items go in at once, hands rest on the bag, and bags shift. A per-item weight delta assumes a settled scale between drops. | 🟠 | Use **one order-level weighing at sealing** (cheap and robust): compare the sum of expected gross weights with the measured weight. Per-item vision handles identity; weight handles completeness. |
| I5 | **Unit economics are missing.** Camera, load cell and edge GPU (Orin-class) per packing station, times stations, times 1,000+ dark stores, plus maintenance. There's no ROI model. | 🟠 | Build a cost-per-order model against the baseline refund/error rate from Instamart data (get real numbers; don't assume them). Look at a cheaper SKU: one overhead camera plus CPU/NPU inference, or a phone mount. Pilot on one station first. |
| I6 | **Catalog churn.** A dark store carries thousands of SKUs, with weekly launches, packaging redesigns, festive/combo/"20% extra" packs and regional variants. The bank has 68 SKUs from Open Food Facts. | 🔴 | **Enroll anchors automatically at inwarding (GRN):** capture 4–6 views when a new SKU or batch is received. Start from Instamart's own catalog images. Monitor drift, flagging SKUs whose live similarity distribution shifts. |
| I7 | **Produce freshness is framed too narrowly.** Instamart's FnV receiving grades against size, colour, defect and weight specs per commodity, by sampling crates. A generic fresh/semi/rotten classifier on a single image doesn't match that process. | 🟠 | Use per-commodity specs and multi-image crate sampling, with defect detection (bruise, mould, cut) rather than a single global label. Report "% of sample defective", which is how vendor penalties are actually computed. |
| I8 | **Expiry OCR at GRN duplicates WMS data.** Many suppliers send batch and expiry details in advance shipment notices (ASNs), and GRN staff record them. The bigger customer pain is near-expiry items reaching orders. | 🟡 | Position OCR as **verification of ASN/WMS expiry** and add a **pick-time near-expiry check**. |
| I9 | **Worker surveillance, privacy and DPDP Act 2023.** Continuous cameras on packers raise labour-relations and consent issues. Faces and customer names on labels can be captured. | 🟠 | Use a bag-mouth-only ROI, on-edge processing, face and label blurring, short retention, and a published purpose limit (QC, not performance policing). Get legal sign-off. |

---

## 2. Implementation (code-level)

### 2.1 Security and trust boundary

| # | Loophole | Sev | Fix |
|---|---|---|---|
| C1 | **The client controls its own tolerance and expected weight.** [`PackingAuditRequest`](file:///d:/Downloads/projects/Instamart_QC/src/insta_audit/schemas.py#L7-L18) accepts `max_weight_error_pct` (up to 100%) and `expected_unit_weight_g` from the caller. A buggy or gamed edge client can set a 100% tolerance and turn off the weight check. The per-SKU `weight_tolerance_pct` in [configs/skus.yaml](file:///d:/Downloads/projects/Instamart_QC/configs/skus.yaml) is never read. | 🔴 | Look up weight and tolerance **server-side** from the catalog by `expected_sku`, or better, fetch order lines from the OMS by `order_id`. Drop or ignore the client-supplied fields, or accept them only for debugging when not in production. |
| C2 | **`observed_quantity` and `observed_sku` come from the client.** The quantity check in [`audit_drop`](file:///d:/Downloads/projects/Instamart_QC/src/insta_audit/decision.py#L14-L23) trusts a count that nothing in the system measures. | 🔴 | Derive the count from weight, \(\hat n = \text{round}(\text{measured}/\text{unit})\), and/or from detector tracks. Flag when the counts disagree. Remove `observed_sku` from the production schema. |
| C3 | **Setting `demo_ui=true` in production turns off authentication.** [main.py L35](file:///d:/Downloads/projects/Instamart_QC/src/insta_audit/api/main.py#L35-L36) skips the API-key requirement, and [Dockerfile.demo](file:///d:/Downloads/projects/Instamart_QC/Dockerfile.demo#L6) sets exactly that. Every endpoint is then public and unauthenticated, and each accepts 7 MB base64 images that trigger CPU-heavy DINOv2 inference: an easy DoS. | 🔴 | Keep demo UI and auth separate. Keep the key on `/v1/*`, or allow only `/v1/catalog/match` and `/v1/inward/freshness` publicly with **rate limiting** (e.g. slowapi). Lower `max_image_bytes` to about 1 MB, cap concurrency with a semaphore, and add request timeouts. |
| C4 | **No audit trail.** Logs record only the decision and reasons, with no `event_id`, `order_id`, model, bank or policy version. The responses don't include versions either, although [architecture.md](file:///d:/Downloads/projects/Instamart_QC/docs/architecture.md#L13) requires them. | 🟠 | Add `model_version`, `bank_version` (hash of the npz), `policy_version` and `latency_ms` to every response and to a structured JSON log. |

### 2.2 Packing decision logic

| # | Loophole | Sev | Fix |
|---|---|---|---|
| C5 | **The weight tolerance is a single percentage.** At 18%, a 6 g Maggi Masala-ae-Magic sachet gets ±1 g, which is below scale noise, so it always holds. A 1 kg salt pack gets ±180 g, enough to hide a whole missing 150 g item. | 🔴 | Use \(\text{tol} = \max(k\sigma_{\text{scale}},\ p\cdot w)\) with per-SKU p learned from real weighings (mean ± 3σ). For SKUs under about 3× scale resolution, mark weight as "uninformative" and rely on vision. |
| C6 | **Net and gross weight are confused.** [populate_multiview_anchors.py](file:///d:/Downloads/projects/Instamart_QC/scripts/populate_multiview_anchors.py#L276-L284) writes the pack's **net** weight with `weight_verified: True`. The scale measures **gross** weight: packaging, nitrogen-flushed bags, and the overfill Legal Metrology allows. For chips that is a systematic +5–10% bias that uses up half the tolerance. | 🟠 | Set `weight_verified` only after weighing N real units. Store `gross_mean_g` and `gross_std_g` per SKU. |
| C7 | **The expected weight uses `observed_quantity`.** In [decision.py L16](file:///d:/Downloads/projects/Instamart_QC/src/insta_audit/decision.py#L16), weight can never catch a quantity error on its own; it just agrees with whatever count was reported. | 🟠 | Compare against `expected_quantity` and report the inferred count (see C2). |
| C8 | **Top-1 match, one global threshold of 0.5, no margin, no open-set handling.** [`SkuBank.match`](file:///d:/Downloads/projects/Instamart_QC/src/insta_audit/vision/sku_bank.py#L27-L35) always returns the nearest SKU, even for a product that isn't in the bank. A DINOv2 CLS cosine of 0.5 is very permissive, and lookalikes (Lay's flavours, Maggi variants) score close together. | 🔴 | Return top-k. Require **s₁ ≥ τ_sku and s₁ − s₂ ≥ margin**. Tune τ per SKU or per lookalike group on real captures. Use weight to break ties within the top-k. Add an "unknown item" path. Consider fine-tuning (metric learning) on packing-camera crops. |
| C9 | **A low-confidence mismatch becomes a hard hold.** If similarity is below the threshold *and* the top SKU differs, the item is held, so the model's uncertainty is billed to the packer. | 🟠 | Decision matrix: confident mismatch → hold; low confidence → review/advisory; confident match and weight OK → pass. |
| C10 | **Model/bank mismatches fail silently.** A dimension mismatch returns `None` ([sku_bank.py L31](file:///d:/Downloads/projects/Instamart_QC/src/insta_audit/vision/sku_bank.py#L31-L32)), and the embedder catches every exception ([embedder.py L41](file:///d:/Downloads/projects/Instamart_QC/src/insta_audit/vision/embedder.py#L41-L42)). Either way every audit quietly becomes `review_required`. | 🟠 | At startup, check `embedding_model` in the bank metadata against the embedder and **fail fast**. Log exceptions. Report the mismatch on `/health`. |
| C11 | **`reconcile_order` doesn't validate counts.** Negative or zero counts are accepted, and dict sizes are unbounded. | 🟡 | Use `dict[str, conint(ge=0)]` with a max length. |

### 2.3 Expiry

| # | Loophole | Sev | Fix |
|---|---|---|---|
| C12 | **The first date in the text wins.** Indian packs print `MFG/PKD dd/mm/yy … EXP/BEST BEFORE …`. [`parse_expiry`](file:///d:/Downloads/projects/Instamart_QC/src/insta_audit/quality/expiry.py#L11-L41) removes "EXP" and returns the first match, which is usually the **manufacture date**, so fresh stock gets rejected as expired. | 🔴 | Tokenise around keywords (`MFG/PKD/MFD` versus `EXP/USE BY/BB/BEST BEFORE`). Pick the date nearest the expiry keyword, or the latest date. Add tests with both dates present. |
| C13 | **"Best before N months from manufacture" isn't handled.** This format is common on Indian FMCG. So are `MM/YY` and `MMM YYYY`. | 🟠 | Parse relative shelf life and compute expiry = MFG + N months. Add `MM/YY` and `MMM YY(YY)` patterns. |
| C14 | **Category shelf life is too coarse, and the category keys don't match.** The policy has `dairy` at 7 days: pouch milk lasts about 2 days (always held) and UHT milk about 180 (always accepted). The catalog uses `snacks_and_namkeen`, `beverages` and so on, which aren't in [quality_policy.yaml](file:///d:/Downloads/projects/Instamart_QC/configs/quality_policy.yaml). **64 of 68 SKUs → `unknown_category`.** | 🔴 | Compute remaining-life % from **this pack's own MFG→EXP span**, and fall back to per-SKU then per-category values. Align the taxonomy and add a unit test that every catalog category exists in the policy. |
| C15 | **A typed `expiry_date` overrides OCR with no evidence kept.** | 🟡 | Store the image and OCR text alongside any manual override, and flag events where the two disagree. |
| C16 | **The UI's expiry logic is duplicated in JS and inverted.** [app.js L140](file:///d:/Downloads/projects/Instamart_QC/app.js#L140) sets `packaged = sku.includes('tomatoes')` and hard-codes 50/70. It doesn't call `/v1/inward/expiry`. | 🟡 | Call the API and delete the client-side policy. |

### 2.4 Freshness

| # | Loophole | Sev | Fix |
|---|---|---|---|
| C17 | **Likely split leakage.** AgriFreshNET is a time-lapse of the same produce items over several days. [prepare_agrifreshnet.py](file:///d:/Downloads/projects/Instamart_QC/scripts/prepare_agrifreshnet.py#L39-L40) groups only by filename (augmented copies), not by physical item or session. The same fruit, background and lighting can land in both train and test. **Best epoch = 1** with val ≈ test ([metadata](file:///d:/Downloads/projects/Instamart_QC/artifacts/freshness_v3.ts.metadata.json#L28)) supports this. | 🟠 | Group by item, sequence or capture session (from folder structure or EXIF). Report per-commodity results. Also do a leave-one-commodity-out evaluation. |
| C18 | **No out-of-distribution gate, and confidence isn't calibrated.** Any image, even a chips packet, gets a fresh/rotten label. The 0.80 softmax cut-off is arbitrary. `FreshnessRequest.sku` is ignored. | 🟠 | Calibrate with temperature scaling. Add an OOD/commodity check (DINOv2 similarity to commodity prototypes). Route per commodity. |
| C19 | **Semi-fresh is the weak class.** About 16% of semi-fresh items are predicted fresh, and 14% of fresh items semi-fresh. Semi-fresh maps to "hold", which isn't an operational grade. | 🟡 | Map classes to Instamart's grade/discount actions. Choose the threshold for the cost of a missed rotten item versus a false reject. |
| C20 | **Checkpoints are selected on recall alone.** An "always rotten" model scores 1.0. | 🟡 | Select on recall at a fixed precision, or on F2. |

### 2.5 Sensors and tracking

| # | Loophole | Sev | Fix |
|---|---|---|---|
| C21 | **Scale handling is too simple.** [`LoadCellCalibration`](file:///d:/Downloads/projects/Instamart_QC/src/insta_audit/sensors/load_cell.py#L9-L10) clamps negatives to 0, which hides items being taken back out. Calibration is single-point. There's no tare, settle detection or temperature drift handling. | 🟠 | Use signed deltas, auto-tare per bag, a stability window (e.g. σ < 1 g over 300 ms), multi-point calibration and periodic drift checks. |
| C22 | **The crossing trigger only watches the y-axis.** Hands crossing the line trigger events, occlusion causes ID switches (double counts), and `out_of_bag` events are dropped in [track_video.py L43](file:///d:/Downloads/projects/Instamart_QC/scripts/track_video.py#L43). | 🟠 | Use a polygon ROI. Fire an event only when a crossing *and* a settled scale step coincide. Subtract `out_of_bag` events. Suppress hand-only tracks. |

### 2.6 Packaging and deployment

| # | Loophole | Sev | Fix |
|---|---|---|---|
| C23 | **The Docker image will likely crash at startup.** [main.py L42](file:///d:/Downloads/projects/Instamart_QC/src/insta_audit/api/main.py#L42-L45) finds `configs/` via `Path(__file__).parents[3]`. Both Dockerfiles use a **non-editable** `pip install .`, so `__file__` sits in `site-packages` and the path resolves to `/usr/local/lib/python3.11/configs` → `FileNotFoundError`. The demo HTML paths break the same way. | 🔴 | Add an `INSTA_AUDIT_POLICY_PATH` / `INSTA_AUDIT_PROJECT_ROOT` setting (defaulting to the CWD, `/app`), or package the configs as package data. Add a CI step that builds the image and calls `/health`. |
| C24 | **Inference runs inside the request.** Sync handlers run DINOv2 on CPU in FastAPI's threadpool (about 40 threads), with no batching or back-pressure. | 🟡 | Use a bounded worker queue or semaphore, plus an ONNX/TensorRT runtime on the edge device. |
| C25 | **Docs and artifacts disagree.** The README says 66 SKUs, the bank metadata says 68 SKUs / 108 views, and the eval used 54. The demo image ships `freshness_v3.ts` (45 MB) unnecessarily. | 🟡 | Generate README numbers from the metadata. Copy only the artifacts actually served. |
| C26 | **Tests and CI are thin.** Nothing covers MFG+EXP strings, light-SKU tolerance, auth with `demo_ui`, category coverage, or the Docker start-up path. | 🟡 | Add these tests (they map directly to C3, C5, C12, C14, C23). |

---

## 3. Evaluation and production generalisation

| # | Loophole | Sev | Fix |
|---|---|---|---|
| G1 | **Same-source evaluation.** [simulate_packing_eval.py](file:///d:/Downloads/projects/Instamart_QC/scripts/simulate_packing_eval.py#L72-L78) builds queries by corrupting the *anchor image itself*. That measures augmentation robustness, not recognition. | 🔴 | Use held-out views: a different OFF uploader, or your own phone or camera captures. Split by capture session. |
| G2 | **The "variant swap" negative is random.** `rng.choice(other skus)` mostly pairs unrelated products, which makes 100% easy. The bank *does* contain real lookalikes (Lay's ×4, Maggi ×3, Kurkure ×3, Parle ×3). | 🔴 | Draw swaps from the **same brand or lookalike group**, using the *other* SKU's real weight. Report the miss rate on hard negatives. |
| G3 | **`missing_unit` and `weight_anomaly` are tautologies.** The first passes an oracle `observed_quantity=1`; the second uses 30–60% weight loss against an 18% tolerance. | 🟠 | Simulate a missing unit from weight only. Use subtle anomalies (5–15%) and a realistic noise model (gross bias, scale σ, packing jitter). |
| G4 | **The metrics are per item, with no confidence intervals.** | 🟠 | Report per-order false holds, miss rate at a fixed false-hold rate, review rate and bootstrap CIs, plus a per-SKU confusion matrix. |
| G5 | **Domain gap.** Studio or consumer photos differ from a top-down bag-mouth camera with motion blur, hands, dark-store LED lighting, glare on metallised packs, opaque paper bags and partial views. | 🔴 | Collect a small **real capture set**: one station, a few hundred orders, labelled. Fine-tune the embedder on it, and re-enroll anchors from the same camera. |
| G6 | **No drift or kill-switch implementation.** The docs mention them, but nothing exists in code. | 🟠 | Add a feature-flag table (advisory/hold/off per store and SKU) and per-SKU similarity-distribution drift alerts. |
| G7 | **Multi-bag, cold-chain and variable-weight items aren't modelled.** Frozen goods go in separate bags, and produce is sold by weight within a band. | 🟠 | Add `bag_id` to events. Give variable-weight SKUs a range in the catalog (min/max) instead of a single unit weight. |

---

## 4. Instamart / quick-commerce acceptance risks

| # | Risk | Fix |
|---|---|---|
| A1 | **No integration path.** Nothing connects to the picker app, WMS or OMS, and the API trusts payloads instead of order data. | Show an adapter contract: a webhook from the OMS on "order ready to pack", with results posted back to the packer app. |
| A2 | **Throughput isn't measured.** Ops will judge seconds added per order, not accuracy. | Run a time-and-motion study in shadow mode and report P50/P95 packing time with and without the system. |
| A3 | **Upkeep across 1,000+ stores.** Camera misalignment, lighting changes and scale drift. | Add self-diagnostics: a reference-target image check, a known-weight check at shift start, and a remote health dashboard. |
| A4 | **No baseline numbers.** "Reduces refunds" is unproven, and the walkthrough correctly forbids claiming it. | Propose a pilot with a pre-agreed metric: wrong/missing-item complaints per 1,000 orders at a pilot store versus a control store over 4 weeks. |
| A5 | **Licensing.** Open Food Facts images are CC BY-SA, and the derived bank may carry share-alike and attribution duties. `amazon_pics/` holds scraped Amazon images (Terms of Service and copyright risk). Ultralytics is AGPL. AgriFreshNET's terms need checking. The OFF User-Agent uses a made-up contact (`student-eval@instamart.qc`). | Exclude `amazon_pics` from any bank. Ship an `ATTRIBUTION.md`. Replace Ultralytics with an Apache-licensed detector (RT-DETR from HF, or YOLOX). Use a real contact address. Base production on Instamart-owned images. |
| A6 | **The pitch leads with the wrong thing.** "Visual SKU verification" sounds redundant next to barcode scans. | Lead with **proof-of-pack plus completeness by weight plus near-expiry at pick**, and keep vision as a secondary check. |

---

## 5. Suggested priority order

1. **Quick fixes (1–2 days):** C1, C3, C12, C14, C23, then their tests (C26).
2. **Make the decision logic trustworthy:** C2/C7 (weight-derived count), C5/C6 (gross weight with absolute and relative tolerance), C8/C9 (top-k, margin, open-set handling), C4 (versioned audit log).
3. **Make the evaluation honest:** G1–G4. Re-run and report per-order false holds on hard negatives.
4. **Real data:** a capture set from one station (G5), multi-view enrollment at GRN (I6), freshness re-split by item (C17).
5. **Reposition for acceptance:** I1/I2/A6 framing, an ROI model (I5), and a shadow-mode pilot plan (A2, A4).
