import argparse
import copy
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a produce freshness classification model on GPU/CPU with early stopping")
    parser.add_argument("--data", type=Path, required=True, help="ImageFolder root with train/ and val/ (optional test/ is scored once at the end)")
    parser.add_argument("--output", type=Path, default=Path("artifacts/freshness.ts"))
    parser.add_argument("--epochs", type=int, default=15)
    parser.add_argument("--batch-size", type=int, default=32, help="Batch size (32 uses ~600MB VRAM on GPU)")
    parser.add_argument("--workers", type=int, default=0, help="DataLoader workers (0 recommended on Windows to avoid spawn overhead)")
    parser.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"], help="Device to train on ('cuda', 'cpu', or 'auto')")
    parser.add_argument("--lr", type=float, default=2e-4, help="Learning rate for AdamW")
    parser.add_argument("--patience", type=int, default=3, help="Early stopping patience (epochs without validation improvement). <=0 disables.")
    parser.add_argument("--min-delta", type=float, default=0.001, help="Minimum improvement required to reset early stopping patience")
    args = parser.parse_args()

    try:
        import torch
        from torch import nn
        from torch.utils.data import DataLoader
        from torchvision import datasets, models, transforms
    except ImportError as exc:
        raise SystemExit('Install optional training dependencies: pip install -e ".[vision,training]"') from exc

    # Device selection and explicit verification
    if args.device == "cuda" and not torch.cuda.is_available():
        raise SystemExit("CUDA was explicitly requested via --device cuda, but no CUDA-capable GPU was detected by PyTorch.")
    
    device = "cuda" if (args.device == "cuda" or (args.device == "auto" and torch.cuda.is_available())) else "cpu"
    if device == "cuda":
        torch.backends.cudnn.benchmark = True
        gpu_name = torch.cuda.get_device_name(0)
        gpu_mem = torch.cuda.get_device_properties(0).total_memory / (1024 ** 2)
        print(f"[Hardware] Training on GPU: {gpu_name} ({gpu_mem:.0f} MB VRAM)")
    else:
        print("[Hardware] Training on CPU")

    mean, std = (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)
    train_tf = transforms.Compose([
        transforms.Resize(256),
        transforms.RandomResizedCrop(224, scale=(0.7, 1.0)),
        transforms.RandomHorizontalFlip(),
        transforms.ColorJitter(0.2, 0.2, 0.2, 0.08),
        transforms.ToTensor(),
        transforms.Normalize(mean, std),
    ])
    val_tf = transforms.Compose([
        transforms.Resize(256),
        transforms.CenterCrop(224),
        transforms.ToTensor(),
        transforms.Normalize(mean, std),
    ])

    train_data = datasets.ImageFolder(args.data / "train", transform=train_tf)
    val_data = datasets.ImageFolder(args.data / "val", transform=val_tf)
    normalize = lambda name: name.strip().lower().replace("-", "_").replace(" ", "_")
    classes = [normalize(name) for name in train_data.classes]
    allowed = ({"fresh", "spoiled"}, {"fresh", "rotten", "semi_fresh"})
    if set(classes) not in allowed or [normalize(name) for name in val_data.classes] != classes:
        raise SystemExit("Expected matching train/val folders: fresh + spoiled, or fresh + semi_fresh + rotten")

    rotten_index = next(i for i, name in enumerate(classes) if name in {"spoiled", "rotten"})
    pin_memory = device == "cuda"
    train_loader = DataLoader(train_data, batch_size=args.batch_size, shuffle=True, num_workers=args.workers, pin_memory=pin_memory)
    val_loader = DataLoader(val_data, batch_size=args.batch_size, shuffle=False, num_workers=args.workers, pin_memory=pin_memory)

    model = models.resnet18(weights=models.ResNet18_Weights.DEFAULT)
    model.fc = nn.Linear(model.fc.in_features, len(classes))
    model.to(device)

    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    loss_fn = nn.CrossEntropyLoss()
    scaler = torch.amp.GradScaler("cuda", enabled=device == "cuda")

    best_recall = -1.0
    best_f1 = -1.0
    best_epoch = 0
    best_metrics = {}
    epochs_no_improve = 0
    early_stopped = False

    args.output.parent.mkdir(parents=True, exist_ok=True)
    print(f"[Training] {len(classes)} classes: {classes} | Train samples: {len(train_data)} | Val samples: {len(val_data)}")
    print(f"[Early Stopping] Patience: {args.patience} epochs | Min delta: {args.min_delta} on spoiled_recall\n")

    for epoch in range(args.epochs):
        model.train()
        running_loss = 0.0
        for images, labels in train_loader:
            images = images.to(device, non_blocking=pin_memory)
            labels = labels.to(device, non_blocking=pin_memory)
            optimizer.zero_grad(set_to_none=True)
            with torch.amp.autocast(device_type=device, enabled=device == "cuda"):
                logits = model(images)
                loss = loss_fn(logits, labels)
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()
            running_loss += loss.item() * images.size(0)

        # Validation evaluation
        model.eval()
        tp = fp = fn = correct = count = 0
        with torch.inference_mode():
            for images, labels in val_loader:
                images = images.to(device, non_blocking=pin_memory)
                logits = model(images)
                pred = logits.argmax(1).cpu()
                correct += int((pred == labels).sum())
                count += len(labels)
                tp += int(((pred == rotten_index) & (labels == rotten_index)).sum())
                fp += int(((pred == rotten_index) & (labels != rotten_index)).sum())
                fn += int(((pred != rotten_index) & (labels == rotten_index)).sum())

        recall = tp / max(1, tp + fn)
        precision = tp / max(1, tp + fp)
        f1 = 2 * precision * recall / max(1e-12, precision + recall)
        avg_loss = running_loss / max(1, len(train_data))
        val_acc = correct / max(1, count)

        # Improvement check: primary is spoiled_recall; tie-breaker is spoiled_f1
        improved = False
        if recall > best_recall + args.min_delta:
            improved = True
        elif abs(recall - best_recall) <= args.min_delta and f1 > best_f1 + args.min_delta:
            improved = True

        status_marker = "(* best checkpoint saved)" if improved else ""
        print(f"epoch={epoch+1:02d}/{args.epochs:02d} loss={avg_loss:.4f} val_acc={val_acc:.4f} spoiled_precision={precision:.4f} spoiled_recall={recall:.4f} spoiled_f1={f1:.4f} {status_marker}")

        if improved:
            best_recall = recall
            best_f1 = f1
            best_epoch = epoch + 1
            epochs_no_improve = 0
            best_metrics = {
                "val_accuracy": val_acc,
                "spoiled_precision": precision,
                "spoiled_recall": recall,
                "spoiled_f1": f1,
                "best_epoch": best_epoch,
            }
            # Script a CPU clone so deployment TorchScript is device-agnostic
            cpu_clone = copy.deepcopy(model).cpu().eval()
            scripted = torch.jit.script(cpu_clone)
            scripted.save(str(args.output))
            del cpu_clone
        else:
            epochs_no_improve += 1
            if args.patience > 0 and epochs_no_improve >= args.patience:
                early_stopped = True
                print(f"\n[Early Stopping] Triggered at epoch {epoch+1}: no improvement for {epochs_no_improve} consecutive epochs.")
                print(f"[Early Stopping] Restoring best checkpoint from epoch {best_epoch} (spoiled_recall={best_recall:.4f}, spoiled_f1={best_f1:.4f}).\n")
                break

    # Untouched test split evaluation using the saved best checkpoint
    test_metrics = None
    if (args.data / "test").is_dir():
        best = torch.jit.load(str(args.output), map_location=device).eval()
        test_data = datasets.ImageFolder(args.data / "test", transform=val_tf)
        if [normalize(name) for name in test_data.classes] != classes:
            raise SystemExit("test/ class folders do not match train/")
        test_loader = DataLoader(test_data, batch_size=args.batch_size, shuffle=False, num_workers=args.workers, pin_memory=pin_memory)
        confusion = [[0] * len(classes) for _ in classes]
        with torch.inference_mode():
            for images, labels in test_loader:
                images = images.to(device, non_blocking=pin_memory)
                preds = best(images).argmax(1).cpu().tolist()
                for truth, pred in zip(labels.tolist(), preds):
                    confusion[truth][pred] += 1
        total = sum(map(sum, confusion))
        tp = confusion[rotten_index][rotten_index]
        fn = sum(confusion[rotten_index]) - tp
        fp = sum(confusion[r][rotten_index] for r in range(len(classes))) - tp
        precision, recall = tp / max(1, tp + fp), tp / max(1, tp + fn)
        f1 = 2 * precision * recall / max(1e-12, precision + recall)
        test_metrics = {
            "n": total,
            "accuracy": sum(confusion[i][i] for i in range(len(classes))) / max(1, total),
            "spoiled_precision": precision,
            "spoiled_recall": recall,
            "spoiled_f1": f1,
            "confusion_rows_truth_cols_pred": {"classes": classes, "matrix": confusion},
        }
        print("TEST (untouched split): " + json.dumps({k: v for k, v in test_metrics.items() if k != "confusion_rows_truth_cols_pred"}))

    metadata = {
        "architecture": "torchvision.resnet18",
        "classes": classes,
        "input_size": [224, 224],
        "normalization_mean": list(mean),
        "normalization_std": list(std),
        "selection_metric": "rotten_or_spoiled_recall",
        "best_validation_metrics": best_metrics,
        "test_metrics": test_metrics,
        "validation_data": str(args.data / "val"),
        "early_stopping": {
            "patience": args.patience,
            "min_delta": args.min_delta,
            "early_stopped": early_stopped,
            "best_epoch": best_epoch,
            "total_epochs": epoch + 1,
        },
        "note": "Tune decision threshold on an untouched store/day set before deployment.",
    }
    args.output.with_suffix(args.output.suffix + ".metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"\nSaved best checkpoint (epoch {best_epoch}) to {args.output}")


if __name__ == "__main__":
    main()
