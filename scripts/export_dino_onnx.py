import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a locally provisioned DINOv2 backbone for edge compilation")
    parser.add_argument("--model-dir", type=Path, required=True, help="local Hugging Face snapshot; no startup download")
    parser.add_argument("--output", type=Path, default=Path("artifacts/dinov2_small.onnx"))
    parser.add_argument("--height", type=int, default=224)
    parser.add_argument("--width", type=int, default=224)
    args = parser.parse_args()
    try:
        import torch
        from transformers import AutoModel
    except ImportError as exc:
        raise SystemExit('Install vision dependencies: pip install -e ".[vision]"') from exc
    model = AutoModel.from_pretrained(args.model_dir, local_files_only=True).eval().cpu()

    class EmbeddingHead(torch.nn.Module):
        def __init__(self, backbone):
            super().__init__()
            self.backbone = backbone

        def forward(self, pixel_values):
            cls = self.backbone(pixel_values=pixel_values).last_hidden_state[:, 0]
            return torch.nn.functional.normalize(cls, dim=1)

    wrapper = EmbeddingHead(model).eval()
    dummy = torch.randn(1, 3, args.height, args.width)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        wrapper, dummy, str(args.output), input_names=["pixel_values"], output_names=["embedding"],
        dynamic_axes={"pixel_values": {0: "batch"}, "embedding": {0: "batch"}},
        opset_version=17, dynamo=False,
    )
    print(f"Exported ONNX backbone to {args.output}; validate numerical parity and latency on the target device.")


if __name__ == "__main__":
    main()
