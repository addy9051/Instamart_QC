import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a two-class produce freshness model")
    parser.add_argument("--data", type=Path, required=True, help="ImageFolder root with train/ and val/")
    parser.add_argument("--output", type=Path, default=Path("artifacts/freshness.ts"))
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--batch-size", type=int, default=32)
    args = parser.parse_args()
    try:
        import torch
        from torch import nn
        from torch.utils.data import DataLoader
        from torchvision import datasets, models, transforms
    except ImportError as exc:
        raise SystemExit('Install optional training dependencies: pip install -e ".[vision,training]"') from exc

    mean, std = (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)
    train_tf = transforms.Compose([
        transforms.Resize(256), transforms.RandomResizedCrop(224, scale=(0.7, 1.0)),
        transforms.RandomHorizontalFlip(), transforms.ColorJitter(.2, .2, .2, .08),
        transforms.ToTensor(), transforms.Normalize(mean, std),
    ])
    val_tf = transforms.Compose([transforms.Resize(256), transforms.CenterCrop(224), transforms.ToTensor(), transforms.Normalize(mean, std)])
    train_data = datasets.ImageFolder(args.data / "train", transform=train_tf)
    val_data = datasets.ImageFolder(args.data / "val", transform=val_tf)
    if train_data.classes != ["fresh", "spoiled"] or val_data.classes != train_data.classes:
        raise SystemExit("Expected ImageFolder class directories exactly: fresh/ and spoiled/")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    train_loader = DataLoader(train_data, batch_size=args.batch_size, shuffle=True, num_workers=4)
    val_loader = DataLoader(val_data, batch_size=args.batch_size, shuffle=False, num_workers=4)
    model = models.resnet18(weights=models.ResNet18_Weights.DEFAULT)
    model.fc = nn.Linear(model.fc.in_features, 2)
    model.to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=2e-4, weight_decay=1e-4)
    loss_fn = nn.CrossEntropyLoss()
    best_recall = -1.0
    best_metrics = {}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for epoch in range(args.epochs):
        model.train()
        running = 0.0
        for images, labels in train_loader:
            images, labels = images.to(device), labels.to(device)
            optimizer.zero_grad(set_to_none=True)
            loss = loss_fn(model(images), labels)
            loss.backward()
            optimizer.step()
            running += loss.item() * images.size(0)
        model.eval()
        tp = fp = fn = correct = count = 0
        with torch.inference_mode():
            for images, labels in val_loader:
                logits = model(images.to(device))
                pred = logits.argmax(1).cpu()
                correct += int((pred == labels).sum())
                count += len(labels)
                tp += int(((pred == 1) & (labels == 1)).sum())
                fp += int(((pred == 1) & (labels == 0)).sum())
                fn += int(((pred == 0) & (labels == 1)).sum())
        recall = tp / max(1, tp + fn)
        precision = tp / max(1, tp + fp)
        f1 = 2 * precision * recall / max(1e-12, precision + recall)
        print(f"epoch={epoch+1}/{args.epochs} loss={running/max(1,len(train_data)):.4f} val_acc={correct/max(1,count):.4f} spoiled_precision={precision:.4f} spoiled_recall={recall:.4f} spoiled_f1={f1:.4f}")
        if recall > best_recall:
            best_recall = recall
            best_metrics = {"val_accuracy": correct / max(1, count), "spoiled_precision": precision, "spoiled_recall": recall, "spoiled_f1": f1}
            scripted = torch.jit.script(model.cpu().eval())
            scripted.save(str(args.output))
            model.to(device)
    metadata = {
        "architecture": "torchvision.resnet18",
        "classes": train_data.classes,
        "input_size": [224, 224],
        "normalization_mean": list(mean),
        "normalization_std": list(std),
        "selection_metric": "spoiled_recall",
        "best_validation_metrics": best_metrics,
        "validation_data": str(args.data / "val"),
        "note": "Tune decision threshold on an untouched store/day set before deployment.",
    }
    args.output.with_suffix(args.output.suffix + ".metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"Saved best-recall checkpoint to {args.output}; tune threshold on an untouched operational validation set.")


if __name__ == "__main__":
    main()
