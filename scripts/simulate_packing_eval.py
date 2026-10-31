"""Synthetic drop-to-bag evaluation using the real DINOv2 bank + the real decision logic.

Events are generated from catalog anchor photos with packing-style corruption (rotation, flip,
glare, blur, partial crop). It is an OPTIMISTIC, same-source stress test, not store performance:
anchors and queries share the same underlying photo. Replace the query images with your own
phone/camera captures (see data/README.md) for credible numbers.
"""
import argparse
import json
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

from insta_audit.catalog import load_catalog
from insta_audit.decision import audit_drop
from insta_audit.vision.sku_bank import SkuBank


def corrupt(img: Image.Image, rng: random.Random, strength: float) -> Image.Image:
    img = img.convert("RGB")
    w, h = img.size
    img = img.rotate(rng.uniform(-90, 90) * strength, expand=True, fillcolor=(150, 120, 80))
    if rng.random() < 0.5:
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
    w, h = img.size
    keep = 1 - rng.uniform(0, 0.35) * strength
    cw, ch = int(w * keep), int(h * keep)
    x, y = rng.randint(0, w - cw), rng.randint(0, h - ch)
    img = img.crop((x, y, x + cw, y + ch))
    img = img.filter(ImageFilter.GaussianBlur(rng.uniform(0, 2.5) * strength))
    img = ImageEnhance.Brightness(img).enhance(rng.uniform(0.7, 1.0 + 0.4 * strength))
    arr = np.asarray(img, dtype=np.float32)
    gh, gw = arr.shape[:2]
    cy, cx = rng.randint(0, gh - 1), rng.randint(0, gw - 1)
    yy, xx = np.ogrid[:gh, :gw]
    glare = np.exp(-(((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * (0.25 * max(gh, gw)) ** 2)))
    arr += glare[..., None] * 160 * strength * rng.random()
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default="data/catalog/skus.yaml")
    ap.add_argument("--anchors", type=Path, default=Path("data/anchors"))
    ap.add_argument("--bank", default="artifacts/sku_bank.npz")
    ap.add_argument("--model", default="facebook/dinov2-small")
    ap.add_argument("--per-sku", type=int, default=3)
    ap.add_argument("--strength", type=float, default=1.0)
    ap.add_argument("--threshold", type=float, default=0.78)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--output", type=Path, default=Path("docs/eval_results.json"))
    args = ap.parse_args()

    import torch
    from transformers import AutoImageProcessor, AutoModel

    rng = random.Random(args.seed)
    catalog = {k: v for k, v in load_catalog(args.catalog).items() if v["unit_weight_g"]}
    bank = SkuBank(args.bank)
    proc = AutoImageProcessor.from_pretrained(args.model)
    model = AutoModel.from_pretrained(args.model).eval()

    def embed(img: Image.Image) -> list[float]:
        with torch.inference_mode():
            v = model(**proc(images=img, return_tensors="pt")).last_hidden_state[:, 0]
            return torch.nn.functional.normalize(v, dim=1)[0].tolist()

    skus = [s for s in catalog if (args.anchors / s / "catalog_front.jpg").is_file()]
    images = {s: Image.open(args.anchors / s / "catalog_front.jpg") for s in skus}
    events, retrieved, total = [], 0, 0
    for sku in skus:
        w = catalog[sku]["unit_weight_g"]
        for i in range(args.per_sku):
            query = embed(corrupt(images[sku], rng, args.strength))
            top = bank.match(query)
            total += 1
            retrieved += int(top is not None and top[0] == sku)
            base = {"event_id": f"{sku}-{i}", "order_id": f"O-{sku}", "expected_sku": sku,
                    "expected_unit_weight_g": w, "expected_quantity": 1, "observed_quantity": 1,
                    "visual_embedding": query}
            # Correct pick: weight within +-6% of nominal.
            events.append(("pass", "correct", {**base, "measured_weight_delta_g": w * rng.uniform(0.94, 1.06)}))
            # Same-weight variant swap: wrong product photographed, scale reads the expected weight.
            other = rng.choice([s for s in skus if s != sku])
            swap = embed(corrupt(images[other], rng, args.strength))
            events.append(("hold", "variant_swap", {**base, "visual_embedding": swap,
                                                    "measured_weight_delta_g": w * rng.uniform(0.97, 1.03)}))
            # Missing unit: order wants 2, one dropped in.
            events.append(("hold", "missing_unit", {**base, "expected_quantity": 2, "observed_quantity": 1,
                                                    "measured_weight_delta_g": w * rng.uniform(0.95, 1.05)}))
            # Right product, wrong weight (leaking/partial pack).
            events.append(("hold", "weight_anomaly", {**base, "measured_weight_delta_g": w * rng.uniform(0.3, 0.6)}))

    stats: dict[str, dict] = {}
    for truth, scenario, payload in events:
        decision = audit_drop(payload, bank, args.threshold, 18.0)["decision"]
        row = stats.setdefault(scenario, {"truth": truth, "n": 0, "hold": 0, "pass": 0, "review_required": 0})
        row["n"] += 1
        row[decision] += 1
    for scenario, row in stats.items():
        correct = row["pass"] if row["truth"] == "pass" else row["hold"]
        row["correct_rate"] = round(correct / row["n"], 4)
    summary = {
        "disclaimer": "Synthetic same-source stress test on public catalog photos; not store performance.",
        "skus": len(skus), "events": len(events), "augmentation_strength": args.strength,
        "threshold": args.threshold, "top1_retrieval_under_corruption": round(retrieved / total, 4),
        "false_hold_rate": round(stats["correct"]["hold"] / stats["correct"]["n"], 4),
        "scenarios": stats,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

