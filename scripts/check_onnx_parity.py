"""Compare the ONNX embedder with the torch embedder on sample images (run on the target device).

    python scripts/check_onnx_parity.py --onnx artifacts/dinov2_small.onnx --model-dir /opt/hf/dinov2-small \
        --images data/anchors --min-cosine 0.999
"""
import argparse
import base64
import time
from pathlib import Path

import numpy as np

from insta_audit.vision.embedder import DinoEmbedder
from insta_audit.vision.onnx_embedder import OnnxEmbedder


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--onnx", required=True)
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--min-cosine", type=float, default=0.999)
    args = parser.parse_args()

    torch_embedder, onnx_embedder = DinoEmbedder(args.model_dir), OnnxEmbedder(args.onnx)
    if not (torch_embedder.ready and onnx_embedder.ready):
        raise SystemExit("both embedders must load (check --onnx, --model-dir and onnxruntime)")
    files = sorted(p for p in args.images.rglob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png"})[: args.limit]
    if not files:
        raise SystemExit(f"no images under {args.images}")
    cosines, torch_ms, onnx_ms = [], [], []
    for path in files:
        encoded = base64.b64encode(path.read_bytes()).decode()
        start = time.perf_counter()
        a = torch_embedder.from_base64(encoded)
        torch_ms.append((time.perf_counter() - start) * 1000)
        start = time.perf_counter()
        b = onnx_embedder.from_base64(encoded)
        onnx_ms.append((time.perf_counter() - start) * 1000)
        if a is None or b is None:
            raise SystemExit(f"embedding failed for {path}")
        cosines.append(float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))))
    worst = min(cosines)
    print(f"images={len(files)} min_cosine={worst:.6f} mean_cosine={np.mean(cosines):.6f}")
    print(f"latency_ms p50 torch={np.percentile(torch_ms, 50):.1f} onnx={np.percentile(onnx_ms, 50):.1f}")
    if worst < args.min_cosine:
        raise SystemExit(f"parity FAILED: min cosine {worst:.6f} < {args.min_cosine}")
    print("parity OK")


if __name__ == "__main__":
    main()
