import argparse
import csv
import hashlib
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path

from sklearn.model_selection import StratifiedGroupKFold


STAGE_PATTERNS = (
    (re.compile(r"^fresh(?:\s|_|$)", re.I), "fresh"),
    (re.compile(r"^semi[\s_-]*fresh(?:\s|_|$)", re.I), "semi_fresh"),
    (re.compile(r"^rotten(?:\s|_|$)", re.I), "rotten"),
)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
AUGMENTED = re.compile(r"^aug_\d+_", re.I)


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare AgriFreshNET for Insta-Audit freshness training")
    parser.add_argument("--source", type=Path, default=Path("data/inward/raw/agrifreshnet/processed/Processed Data"))
    parser.add_argument("--output", type=Path, default=Path("data/inward/freshness_model"))
    parser.add_argument("--manifest", type=Path, default=Path("data/inward/agrifreshnet_split_manifest.csv"))
    parser.add_argument("--folds", type=int, default=5, help="5 folds gives approximately 60/20/20 train/val/test")
    parser.add_argument("--dry-run", action="store_true", help="print split counts only; copy nothing, write no manifest")
    args = parser.parse_args()

    records = []
    for path in sorted(args.source.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        rel = path.relative_to(args.source)
        top_folder = rel.parts[0]
        stage = next((label for pattern, label in STAGE_PATTERNS if pattern.match(top_folder)), None)
        if stage is None:
            continue
        original_name = AUGMENTED.sub("", path.name)
        group = hashlib.sha256(original_name.casefold().encode("utf-8")).hexdigest()
        records.append({"path": path, "relative": rel.as_posix(), "stage": stage, "group": group,
                        "original_name": original_name})

    if not records:
        raise SystemExit(f"No labeled images found under {args.source}")
    classes = sorted({r["stage"] for r in records})
    expected = ["fresh", "rotten", "semi_fresh"]
    if classes != expected:
        raise SystemExit(f"Expected all three freshness stages {expected}; found {classes}")

    group_labels: dict[str, set[str]] = defaultdict(set)
    group_sizes: Counter[str] = Counter()
    for record in records:
        group_labels[record["group"]].add(record["stage"])
        group_sizes[record["group"]] += 1
    conflicts = [g for g, labels in group_labels.items() if len(labels) > 1]

    # Groups whose filename appears under conflicting labels are unreliable: keep them out of every
    # split (recorded as "excluded" in the manifest) so no label noise leaks into train/val/test.
    # StratifiedGroupKFold keeps filename-linked augmented variants inside one split.
    # Fold 0 -> test (never used for training or checkpoint selection), fold 1 -> val, rest -> train.
    conflict_set = set(conflicts)
    clean_idx = [i for i, r in enumerate(records) if r["group"] not in conflict_set]
    splitter = StratifiedGroupKFold(n_splits=args.folds, shuffle=True, random_state=42)
    y = [records[i]["stage"] for i in clean_idx]
    groups = [records[i]["group"] for i in clean_idx]
    folds = [idx for _, idx in splitter.split(clean_idx, y, groups)]
    test = {clean_idx[k] for k in folds[0]}
    val = {clean_idx[k] for k in folds[1]}
    split_names = ["excluded" if records[i]["group"] in conflict_set else "test" if i in test
                   else "val" if i in val else "train" for i in range(len(records))]

    counts = Counter((split_names[i], record["stage"]) for i, record in enumerate(records))
    if args.dry_run:
        totals = {s: sum(v for (sp, _), v in counts.items() if sp == s) for s in ("train", "val", "test", "excluded")}
        print("DRY RUN " + ", ".join(f"{s}={n}" for s, n in totals.items()) + f"; conflicting groups={len(conflicts)}")
        for split in ("train", "val", "test"):
            print(split + ": " + ", ".join(f"{cls}={counts[(split, cls)]}" for cls in expected))
        return
    args.output.mkdir(parents=True, exist_ok=True)
    manifest_path = args.manifest
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    # The destination is generated output; refuse to mix it with an old, possibly different split.
    if any((args.output / split / cls).exists() for split in ("train", "val", "test") for cls in expected):
        raise SystemExit(f"Output folders already exist: {args.output}. Move them aside before rebuilding the split.")

    with manifest_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["split", "stage", "source_relative_path", "original_filename", "group_id", "group_label_conflict"])
        writer.writeheader()
        for i, record in enumerate(records):
            split = split_names[i]
            if split != "excluded":
                target = args.output / split / record["stage"] / record["path"].name
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    raise SystemExit(f"Duplicate destination filename: {target}")
                shutil.copy2(record["path"], target)
            writer.writerow({"split": split, "stage": record["stage"], "source_relative_path": record["relative"],
                             "original_filename": record["original_name"], "group_id": record["group"],
                             "group_label_conflict": str(len(group_labels[record["group"]]) > 1).lower()})

    counts = Counter((split_names[i], record["stage"]) for i, record in enumerate(records))
    print(f"Prepared {len(records)} images across {len(group_labels)} filename groups at {args.output}")
    totals = {s: sum(v for (sp, _), v in counts.items() if sp == s) for s in ("train", "val", "test", "excluded")}
    print("Split: " + ", ".join(f"{s}={n}" for s, n in totals.items()) + f"; conflicting-label groups={len(conflicts)} (excluded)")
    for split in ("train", "val", "test"):
        print(split + ": " + ", ".join(f"{cls}={counts[(split, cls)]}" for cls in expected))
    if conflicts:
        print(f"Excluded {len(conflicts)} groups marked group_label_conflict=true in {manifest_path}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
