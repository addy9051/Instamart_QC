# Validation plan without store access

Open Food Facts (OFF) photos can bootstrap a prototype but cannot show that Insta-Audit helps dark-store operations (see `docs/loophole_review.md` G1, G5, A4). No public dataset has a top-down packing camera with order lists, scale traces and injected errors. This plan builds the closest honest substitute yourself and defines what you may and may not claim from it.

## 1. Goal and claim boundaries

**Goal:** produce measured, reproducible evidence on real hardware and real packs, so that a conversation with Instamart starts from data instead of a design document.

| You may say | You may not say |
|---|---|
| "On N simulated orders with real packs and a real scale, the per-order false-hold rate was X% (95% CI a–b%) and injected missing items were caught Y% of the time." | "This reduces refunds / complaints by Z%." |
| "Per-SKU gross weight spread is X% on a Y g-resolution scale, which sets the smallest detectable missing item." | "This works in Instamart dark stores." |
| "Recognition on held-out real photos of lookalike packs is X% top-1 / Y% at the chosen margin." | Any claim about packer time, throughput or dispute resolution (not measured here). |

Always label results **simulated, single operator, single station**.

## 2. What you need

- **Packs:** 20–50 real SKUs, 20 or more sealed units of each (more for the SKUs you most want to defend). Prioritise lookalike groups already in `docs/photo_collection_checklist.md` (Lay's, Maggi, Kurkure, Parle flavours), a few very light packs (sachets), a few heavy packs (1 kg), and 2–3 loose or variable-weight items.
- **Scale:** at least 1 g resolution; 0.1 g is better for light packs. Note its model and resolution. Check it with a known reference weight (a coin or calibration weight) before every session.
- **Camera:** a phone on a fixed overhead mount aimed at a shopping-bag mouth. Fix the pose; mark the mount position.
- **Bags:** the same paper or plastic bags every time.
- **People:** you and friends only; no faces in frame, no customer data. Do not film anyone who has not agreed.

## 3. Phase A: scale characterisation

Purpose: set `scale_sigma_g` and know the smallest detectable item.

1. Empty-platform noise: record 30 readings, platform empty. Report mean and standard deviation.
2. Repeatability: place one 100 g object, remove, replace, 30 times. Report standard deviation. Use the larger of the two as `INSTA_AUDIT_SCALE_SIGMA_G`.
3. Linearity: weigh known weights across the range (e.g. 10, 50, 200, 500, 1000 g). Report error at each point; use `scripts/calibrate_scale.py` for a single-point estimate only if you are using a raw load cell.
4. Settle behaviour: with a bag on the scale, drop a 60 g pack in and log how long until readings stay within 1 g. This tells you what `settled` should mean.

**Output:** `data/validation/scale_characterisation.csv` and a two-line summary (sigma_g, smallest reliably detectable item = about 3 x sigma_g).

## 4. Phase B: per-SKU gross weights

Purpose: give the seal check verified gross weights (`gross_mean_g`, `gross_std_g`, `gross_verified`) instead of nominal net weights.

1. Weigh each unit individually, packaging included, 20 or more units per SKU. Where possible buy from different shops or batches; one batch understates real spread.
2. Record to `data/validation/weighings.csv`:

   ```
   sku,unit_id,batch,weight_g,scale_id,session_id,timestamp
   ```
3. Compute mean and sample standard deviation per SKU. Flag SKUs where `std / mean` is above about 3%: they need a wider band or a min/max range.
4. Write results to `data/catalog/skus.yaml` (`gross_mean_g`, `gross_std_g`, `gross_verified: true`, optional `weight_tolerance_pct`; format in `data/README.md`). Only set `gross_verified` for SKUs that had at least 20 weighings.
5. Report the net-versus-gross bias per SKU. This is evidence for fix C6.

**Helper still to write:** a small script that reads `weighings.csv` and updates the catalog. Until then, edit the YAML by hand.

## 5. Phase C: public-data benchmarks (recognition only)

Purpose: replace the same-source eval with cross-domain tests (G1, G2).

| Dataset | Use | Notes |
|---|---|---|
| RPC | Multi-item checkout scenes; hard negatives; count confusion | CC BY-NC-SA: research and benchmarking only |
| Grocery Store Dataset (Klasson et al.) | Catalog images in the bank, real store photos as queries | Check the licence before use |
| Products-10K | Fine-grained lookalike stress test | Non-commercial; benchmarking only |
| Your own phone photos of your Phase B packs | In-domain held-out queries | Best evidence; fully yours |

Rules:
- Bank images and query images must come from different sources or sessions.
- Draw wrong-variant negatives from the same brand or lookalike group, using the other SKU's real weight (G2).
- Report top-1, top-k, margin-based accept/reject, and the unknown-item rate, with bootstrap CIs.
- Keep results from non-commercial datasets labelled as benchmarks. Do not train a model you intend to deploy on them.

## 6. Phase D: simulated packing sessions

Purpose: the closest substitute for a labelled packing set.

### 6.1 Order design

- Build orders of 3–12 items drawn from your SKUs, with 1–2 units per line. Include some multi-bag orders (e.g. an "ambient" and a "frozen" bag) to exercise `bag_id`.
- Write each order to a JSON file in the webhook format (`docs/oms_webhook_contract.md`) and load it with `INSTA_AUDIT_MOCK_ORDERS_PATH`. The "picker scans" in the order file are the intended scan list.

### 6.2 Injected errors

Run a mix of correct and faulty orders. Decide the fault for each order in advance and write it down before packing.

| Class | Share | Examples |
|---|---|---|
| Correct | about 70-80% | Pack exactly the order |
| Missing unit | 5-8% | Leave out one unit of a line |
| Duplicate unit | 5-8% | Add one extra unit |
| Wrong variant | 5-8% | Swap one pack for a lookalike of similar weight |
| Light-item miss | 3-5% | Leave out a sachet-class item |
| Scan skipped | 2-3% | Bag an item that the order's scans list omits |

### 6.3 Capture protocol per order

1. Tare the scale with the empty bag on it.
2. Film from sealing start. Pack items in a natural way, including several at once and hands resting on the bag.
3. Wait until the reading is stable, then record the **settled** weight.
4. Save one row to `data/validation/sessions.jsonl`:

   ```json
   {"session_id": "S01", "order_id": "ORD-0001", "bag_id": "bag-1",
    "planned_fault": "missing_unit", "fault_sku": "8901491101844",
    "seal_weight_g": 412.5, "settled": true,
    "video": "videos/S01/ORD-0001.mp4", "operator": "A"}
   ```
5. Pack in different sessions (different days, lighting, bag positions). Rotate the scale position slightly between sessions.

### 6.4 How many

- **At least 300 correct orders.** With zero false holds in 300 orders, the 95% upper bound on the per-order false-hold rate is about 1% (rule of three, 3 / 300), which is the release gate in `docs/product_direction.md`. Fewer orders cannot support that claim.
- **At least 50 orders per injected error class.** Fewer than that gives confidence intervals too wide to report.
- Split by session before any threshold tuning. Reserve whole sessions as a final untouched test set and look at them once.

### 6.5 Labelling

- The planned fault is the ground truth, because you injected it. Double-check by reviewing each video of a faulty order once.
- Mark an order `ambiguous` if the pack went wrong by accident; exclude it from the main metrics but report the count.
- Keep a second reviewer for any order labelled as correct that the system disputes.

## 7. Phase E: run and score

1. **Weight and count checks only (no vision):** call `/v1/packing/seal` with `measured_weight_g`, `settled`, and `bagged_quantities` taken from your own record of what was packed (or from a tracker if you build one). Store each response.
2. **With vision (optional):** add the vision-derived counts; compare with and without.
3. **Score per order** with `insta_audit.metrics.order_report`: per-order false-hold rate, false-review rate, miss rate, and the gate result using the upper CI bound. Add `miss_rate_at_false_hold_budget` for a fixed false-hold budget.
4. **Break down results** by error class, by SKU weight band (light / medium / heavy), and by operating mode (`shadow`, `advisory`).
5. **Ablate:** report weight-only, count-only and combined, to show what each signal adds. This is the evidence for the "two signals must agree" rule.
6. **Compare with the synthetic baseline:** rerun `scripts/simulate_seal_eval.py` with the sigma and spread you measured in Phases A and B, and compare it with the real session results. Large gaps show where the noise model is wrong.
7. Re-run `scripts/evaluate_audits.py` on any JSONL you produce in its pass/hold format.

## 8. Phase F: report

Produce a short, honest report (a single page is fine):

1. Setup: scale model, resolution, measured sigma, camera, number of SKUs, orders, sessions, operator count.
2. Table of per-order metrics with 95% CIs and the gate result.
3. Detection by injected error class, and the smallest item weight the system can detect.
4. What failed: SKUs with high spread, missed light items, false reviews, and why.
5. Limits: simulated errors, one operator, one station, no real throughput data, no real complaint data.
6. Ask: a one-store shadow pilot (see `docs/product_direction.md` section 11) and the specific data you need from Instamart (catalog images, baseline complaint rates, packing time).

## 9. Risks and how to handle them

| Risk | Handling |
|---|---|
| Self-injected errors are easier than real ones | State it. Make some errors deliberately subtle (a single light item, a same-weight swap). |
| One operator packs consistently | Use several people if possible and report operator as a column. |
| Small SKU set overfits the thresholds | Tune on some sessions, report on untouched sessions. |
| Phone video does not match store cameras | Treat vision numbers as indicative only; weight and count results are the main evidence. |
| Non-commercial dataset licences | Benchmark only; never in a deployed model. |
| Privacy | No faces, no customer data, delete raw video you do not need, keep proof-of-pack disabled unless testing it on your own footage. |

## 10. Deliverables checklist

- [ ] `data/validation/scale_characterisation.csv` and measured `scale_sigma_g`
- [ ] `data/validation/weighings.csv` and updated `gross_*` fields in the catalog
- [ ] Public-data benchmark results with hard negatives and CIs
- [ ] `data/validation/sessions.jsonl` with at least 300 correct and 50 per error class orders, split by session
- [ ] Per-order report from `order_report`, including the gate result
- [ ] One-page report with limits stated
