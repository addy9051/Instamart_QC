"""Build per-commodity DINOv2 prototypes for the freshness out-of-distribution gate (C18).

Expects <root>/<commodity>/**/*.jpg (any stage); writes artifacts/commodity_prototypes.npz with
`commodities` and mean-normalised `embeddings`. Use the same embedding model as the SKU bank.

    python scripts/build_commodity_prototypes.py --root data/inward/commodities --model facebook/dinov2-small
"""
import argparse
from pathlib import Path

import numpy as np
from PIL import Image


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--model", default="facebook/dinov2-small")
    parser.add_argument("--per-commodity", type=int, default=200, help="max images embedded per commodity")
    parser.add_argument("--output", type=Path, default=Path("artifacts/commodity_prototypes.npz"))
    args = parser.parse_args()

    import torch
    from transformers import AutoImageProcessor, AutoModel

    device = "cuda" if torch.cuda.is_available() else "cpu"
    processor = AutoImageProcessor.from_pretrained(args.model, use_fast=True)
    model = AutoModel.from_pretrained(args.model).to(device).eval()
    names, vectors = [], []
    for folder in sorted(p for p in args.root.iterdir() if p.is_dir()):
        files = sorted(f for f in folder.rglob("*") if f.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})
        files = files[:: max(1, len(files) // args.per_commodity)][: args.per_commodity]
        if not files:
            continue
        embeddings = []
        for path in files:
            inputs = processor(images=Image.open(path).convert("RGB"), return_tensors="pt").to(device)
            with torch.inference_mode():
                vector = model(**inputs).last_hidden_state[:, 0]
                embeddings.append(torch.nn.functional.normalize(vector, dim=1)[0].cpu().numpy())
        mean = np.mean(embeddings, axis=0)
        names.append(folder.name.lower())
        vectors.append(mean / max(float(np.linalg.norm(mean)), 1e-12))
        print(f"{folder.name}: {len(files)} images")
    if not names:
        raise SystemExit(f"no commodity folders with images under {args.root}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    np.savez(args.output, commodities=np.array(names), embeddings=np.stack(vectors).astype(np.float32))
    print(f"Wrote {len(names)} prototypes to {args.output} (model {args.model})")


if __name__ == "__main__":
    main()
