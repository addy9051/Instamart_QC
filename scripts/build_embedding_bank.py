import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image
from tqdm import tqdm


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a DINOv2 multi-view SKU reference bank")
    parser.add_argument("--catalog", type=Path, required=True, help="SKU manifest YAML")
    parser.add_argument("--images", type=Path, required=True, help="Root containing <sku>/*.jpg")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="facebook/dinov2-small")
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args()

    import torch
    import yaml
    from transformers import AutoImageProcessor, AutoModel

    with args.catalog.open(encoding="utf-8") as stream:
        sku_ids = {str(row["sku"]) for row in yaml.safe_load(stream).get("skus", [])}
    device = "cuda" if torch.cuda.is_available() else "cpu"
    processor = AutoImageProcessor.from_pretrained(args.model)
    model = AutoModel.from_pretrained(args.model).to(device).eval()
    vectors: list[np.ndarray] = []
    labels: list[str] = []
    extensions = {".jpg", ".jpeg", ".png", ".webp"}
    for sku_dir in sorted(args.images.iterdir()):
        if not sku_dir.is_dir() or sku_dir.name not in sku_ids:
            continue
        paths = [p for p in sku_dir.rglob("*") if p.suffix.lower() in extensions]
        if len(paths) < 1:
            print(f"skip {sku_dir.name}: no anchor images")
            continue
        if len(paths) < 4:
            print(f"warning {sku_dir.name}: only {len(paths)} anchor view(s); add front/back/sides/tilted photos")
        for start in range(0, len(paths), args.batch_size):
            batch_paths = paths[start:start + args.batch_size]
            images = [Image.open(path).convert("RGB") for path in batch_paths]
            inputs = processor(images=images, return_tensors="pt").to(device)
            with torch.inference_mode():
                output = model(**inputs).last_hidden_state[:, 0]
                output = torch.nn.functional.normalize(output, dim=1).cpu().numpy().astype(np.float32)
            vectors.extend(output)
            labels.extend([sku_dir.name] * len(output))
        print(f"embedded {len(paths)} views for {sku_dir.name}")
    if not vectors:
        raise SystemExit("No anchor images matched catalog SKU folders")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(args.output, skus=np.asarray(labels, dtype="U80"), embeddings=np.stack(vectors))
    metadata = {
        "embedding_model": args.model,
        "embedding_dimension": int(vectors[0].shape[0]),
        "reference_views": len(labels),
        "sku_count": len(set(labels)),
        "anchor_root": str(args.images),
        "catalog_manifest": str(args.catalog),
        "data_notice": "Record source and image license/attribution for every anchor set; model bank inherits applicable data terms.",
    }
    args.output.with_suffix(args.output.suffix + ".metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"saved {len(labels)} views ({len(set(labels))} SKUs) to {args.output} on {device}")


if __name__ == "__main__":
    main()
