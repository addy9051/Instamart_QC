# Architecture and rollout notes

## Event path

1. A store adapter reads the order lines, calibrated scale, and camera frames. ByteTrack IDs and a bag-mouth polygon ROI create candidate crossings (`BagMouthTracker`; `track_video.py` is an offline reference path). A crossing only becomes an event when a settled scale step of the same sign coincides (`EventFuser`), so hands, ID switches and items taken back out are not counted as drops.
2. The edge worker crops the object, produces a DINOv2 embedding (torch, or the optional ONNX Runtime embedder), and sends the embedding or a bounded image crop with the measured weight delta to `/v1/packing/audit`. Expected weight, tolerance and identity come from the server-side catalog by `expected_sku`, never from the client. Inference runs in a bounded worker pool with a queue limit and per-call timeout (503 when busy, 504 on timeout).
3. `SkuBank` returns the top-k SKUs. A match is confident only when the best score clears the (per-SKU) floor **and** beats the runner-up by a margin (wider for same-brand/lookalike groups); otherwise it is `ambiguous` (weight can break the tie) or `unknown` (never forced onto the nearest SKU). The unit count is inferred from the weight; only confident vision plus verified-gross weight evidence can produce a hold, and everything else is a review. A missing model/reference match returns review; uncertain candidates must not silently pass. `/health` reports embedder/bank compatibility and startup fails fast on a mismatch.
4. On batch receiving, a close-up date image is OCR'd (when enabled) and parsed into a calendar date. The category policy compares remaining days with a configured reference shelf-life. The reference duration is an approximation until batch manufacture date / vendor policy is available.
5. Produce grading is a triage signal only. The model cannot see hidden spoilage, contamination, temperature abuse, or food-safety conditions.
6. At sealing, the packer app posts one settled bag weight (and optional bagged counts) to `/v1/packing/seal`. The service looks up the order lines and picker scans from the OMS adapter, checks the weight against a gross-weight band, reconciles scans against bagged counts, applies the store's operating mode (`shadow` by default), and, when enabled, stores a redacted, hash-chained proof-of-pack bundle. See `docs/oms_webhook_contract.md` and `docs/privacy_impact_checklist.md`.

## Model and data controls

- Version each SKU bank, model artifact, capture protocol, calibration, and policy. Store their versions in the deployment manifest and audit event.
- Evaluate by held-out store/day/session. Report per-SKU confusion, wrong-variant miss rate, false-hold rate, review rate, latency percentiles, calibration drift, and packer intervention time. Aggregate accuracy alone is not a release gate.
- Tune thresholds for the cost of a missed error versus a false hold. Keep a human-review path and a kill switch; only release a model after an operations-approved shadow-mode period.
- Do not persist raw customer orders, video, or images beyond the approved retention policy. API access control is optional in development and mandatory in production mode.
- The SKU embedder, freshness checkpoint, and optional OCR adapter load only from local artifact paths. The service does not download model weights at startup.

## Practical limits

The public datasets named in the project brief are component benchmarks, not the target domain. Open Food Facts images are catalog anchors, often studio-like and inconsistent. SKU-110K/RPC/MVTec/Products-10K have distinct access and use conditions. Production readiness requires store-owned samples, a calibrated weight stream, a detector/track adapter tuned at the bag ROI, and measured latency on the actual edge hardware.

For edge rollout, export and compile models on a build host that matches the JetPack/TensorRT version on the target Orin device. Validate preprocessing parity, output similarity, precision drift, tail latency, thermal throttling, memory, and camera/scale synchronization on-device. The ONNX export helper is only the starting point; it does not establish a TensorRT engine or a latency SLA.

The example detector trainer uses Ultralytics for iteration convenience. Review its current AGPL/enterprise licensing before commercial deployment. The product code's license does not grant rights to datasets, images, model checkpoints, or third-party libraries.
