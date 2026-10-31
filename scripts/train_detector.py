import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Fine-tune a product detector on labeled store footage")
    parser.add_argument("--data", type=Path, required=True, help="Ultralytics data YAML")
    parser.add_argument("--model", default="yolo11n.pt")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--device", default=None, help="e.g. 0, cpu")
    parser.add_argument("--name", default="insta-audit-localizer")
    args = parser.parse_args()
    if not args.data.is_file():
        raise SystemExit(f"dataset YAML not found: {args.data}")
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise SystemExit('Install optional training dependencies: pip install -e ".[training]"') from exc
    model = YOLO(args.model)
    model.train(
        data=str(args.data), epochs=args.epochs, imgsz=args.imgsz,
        batch=args.batch, device=args.device, name=args.name,
        project="artifacts/detector-runs", patience=20, deterministic=True,
        cache=False, workers=4, seed=42,
    )
    print("Training complete. Validate on an untouched store/day split before deployment.")


if __name__ == "__main__":
    main()
