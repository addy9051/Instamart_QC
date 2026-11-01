"""Single-image catalog lookup demo over Open Food Facts product photos.

With one reference photo per SKU, querying with that same photo is a SELF-MATCH: it proves the
pipeline (image -> DINOv2 -> bank -> SKU) runs, not that a new camera view is recognised. For a
genuine signal pass --query-dir with your own photos named <sku>_<anything>.jpg (new views).

Writes a CSV and prints the summary: sku, name, top1, similarity, runner-up margin, mode.
"""
import argparse
import csv
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from transformers import AutoImageProcessor, AutoModel

from insta_audit.catalog import load_catalog
from insta_audit.vision.sku_bank import SkuBank


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default="data/catalog/skus.yaml")
    ap.add_argument("--anchors", type=Path, default=Path("data/anchors"))
    ap.add_argument("--bank", default="artifacts/sku_bank.npz")
    ap.add_argument("--model", default="facebook/dinov2-small")
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    ap.add_argument("--query-dir", type=Path, default=None, help="new-view photos named <sku>_*.jpg")
    ap.add_argument("--output", type=Path, default=Path("data/catalog/lookup_demo.csv"))
    args = ap.parse_args()

    catalog, bank = load_catalog(args.catalog), SkuBank(args.bank)
    if not bank.ready:
        raise SystemExit(f"SKU bank not found or empty at {args.bank}")

    device = "cuda" if (args.device == "cuda" or (args.device == "auto" and torch.cuda.is_available())) else "cpu"
    proc = AutoImageProcessor.from_pretrained(args.model, use_fast=True)
    model = AutoModel.from_pretrained(args.model).to(device).eval()

    bank_sku_set = set(bank.skus.tolist())
    all_anchor_skus = set()
    if args.query_dir:
        mode = "new_view"
        queries = [(p.name.split("_")[0], p) for p in sorted(args.query_dir.glob("*.[jp][pn]g"))]
    else:
        mode = "self_match_pipeline_demo"
        all_dirs = [d for d in sorted(args.anchors.iterdir()) if (d / "catalog_front.jpg").is_file()]
        all_anchor_skus = {d.name for d in all_dirs}
        # Only query SKUs currently indexed in the SKU bank
        queries = [(d.name, d / "catalog_front.jpg") for d in all_dirs if d.name in bank_sku_set]
        excluded_count = len(all_anchor_skus) - len(queries)
        if excluded_count > 0:
            print(f"[Catalog Filter] Indexing {len(queries)} catalog SKUs; skipping {excluded_count} raw anchor folders not in the curated SKU bank.")

    rows, hits = [], 0
    for sku, path in queries:
        with torch.inference_mode():
            inputs = proc(images=Image.open(path).convert("RGB"), return_tensors="pt")
            inputs = {k: v.to(device) for k, v in inputs.items()}
            v = model(**inputs).last_hidden_state[:, 0]
            v = torch.nn.functional.normalize(v, dim=1)[0].cpu().numpy()
        scores = bank.vectors @ (v / np.linalg.norm(v))
        order = np.argsort(-scores)
        top, second = int(order[0]), int(order[1])
        hit = str(bank.skus[top]) == sku
        hits += hit
        rows.append({"query": path.name, "true_sku": sku, "name": catalog.get(sku, {}).get("name", ""),
                     "top1_sku": str(bank.skus[top]), "similarity": round(float(scores[top]), 4),
                     "margin_vs_runner_up": round(float(scores[top] - scores[second]), 4),
                     "correct": hit, "mode": mode})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"mode={mode} queries={len(rows)} top1_correct={hits} ({hits / max(1, len(rows)):.1%}) [device: {device}] -> {args.output}")
    if mode != "new_view":
        print("NOTE: self-match. This demonstrates the pipeline only; it is not recognition of a new view.")


if __name__ == "__main__":
    main()
