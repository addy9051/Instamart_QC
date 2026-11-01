"""Public-dataset benchmark of the freshness model on the AgriFreshNET held-out TEST split.

Outputs (default: data/inward/report/, git-ignored because of dataset licensing):
  freshness_metrics.json  per-class precision/recall/F1 + confusion matrix, with and without
                          the filename groups whose labels conflict across folders
  freshness_gallery.html  image, source label, model result, confidence, probabilities

IMPORTANT: with --split test the images were never used for training or checkpoint selection (needs a
model trained on a prepare_agrifreshnet.py 3-way split). With --split val the numbers are optimistic.
Quote the "excluding_conflicting_groups" block as a public-dataset benchmark, never Instamart accuracy.
"""
import argparse
import csv
import html
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path

import torch
from PIL import Image
from torchvision import transforms


def prf(conf: dict, classes: list[str]) -> dict:
    out = {}
    for c in classes:
        tp = conf[(c, c)]
        fp = sum(conf[(t, c)] for t in classes if t != c)
        fn = sum(conf[(c, p)] for p in classes if p != c)
        p, r = tp / max(1, tp + fp), tp / max(1, tp + fn)
        out[c] = {"precision": round(p, 4), "recall": round(r, 4),
                  "f1": round(2 * p * r / max(1e-12, p + r), 4), "support": tp + fn}
    return out


def summarize(rows: list[dict], classes: list[str]) -> dict:
    conf: Counter = Counter((r["truth"], r["pred"]) for r in rows)
    n = len(rows)
    return {
        "n": n,
        "accuracy": round(sum(conf[(c, c)] for c in classes) / max(1, n), 4),
        "per_class": prf(conf, classes),
        "confusion_rows_truth_cols_pred": {t: {p: conf[(t, p)] for p in classes} for t in classes},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, default=Path("data/inward/agrifreshnet_split_manifest_complete.csv"))
    ap.add_argument("--images", type=Path, default=Path("data/inward/freshness_model_complete"))
    ap.add_argument("--model", type=Path, default=Path("artifacts/freshness.ts"))
    ap.add_argument("--split", default="test", help="use val only for a labelled-optimistic number")
    ap.add_argument("--out", type=Path, default=Path("data/inward/report"))
    ap.add_argument("--gallery-per-class", type=int, default=8, help="correct examples per class")
    ap.add_argument("--gallery-errors", type=int, default=24)
    ap.add_argument("--review-threshold", type=float, default=0.80)
    args = ap.parse_args()

    if args.split != "test":
        print(f"WARNING: split '{args.split}' was used to pick the checkpoint; metrics are optimistic.")
    meta = json.loads(Path(str(args.model) + ".metadata.json").read_text(encoding="utf-8"))
    classes = meta["classes"]
    model = torch.jit.load(str(args.model), map_location="cpu").eval()
    tf = transforms.Compose([transforms.Resize(256), transforms.CenterCrop(224), transforms.ToTensor(),
                             transforms.Normalize(meta["normalization_mean"], meta["normalization_std"])])

    with args.manifest.open(encoding="utf-8", newline="") as f:
        manifest = [r for r in csv.DictReader(f)]
    # Conflict audit over the WHOLE manifest, before any metric is shown.
    labels_by_group = defaultdict(set)
    for r in manifest:
        labels_by_group[r["group_id"]].add(r["stage"])
    conflicting = {g for g, s in labels_by_group.items() if len(s) > 1}
    conflict_files = sorted({r["original_filename"] for r in manifest if r["group_id"] in conflicting})
    split_rows = [r for r in manifest if r["split"] == args.split]
    if not split_rows:
        raise SystemExit(f"manifest has no '{args.split}' rows; re-run prepare_agrifreshnet.py for a test split, "
                         "or pass --split val for an optimistic number")
    print(f"conflicting-label groups: {len(conflicting)} (rows in manifest: "
          f"{sum(r['group_id'] in conflicting for r in manifest)}, in '{args.split}': "
          f"{sum(r['group_id'] in conflicting for r in split_rows)})")

    rows = []
    batch, batch_meta = [], []

    def flush() -> None:
        if not batch:
            return
        with torch.inference_mode():
            probs = model(torch.stack(batch)).softmax(dim=1)
        for m, p in zip(batch_meta, probs, strict=True):
            conf, idx = p.max(dim=0)
            m.update(pred=classes[int(idx)], confidence=float(conf), probs=[float(x) for x in p])
            rows.append(m)
        batch.clear()
        batch_meta.clear()

    for r in split_rows:
        path = args.images / args.split / r["stage"] / Path(r["source_relative_path"]).name
        if not path.is_file():
            raise SystemExit(f"missing image {path}; rebuild the split or pass --images")
        batch.append(tf(Image.open(path).convert("RGB")))
        batch_meta.append({"path": path, "truth": r["stage"], "conflict": r["group_id"] in conflicting,
                           "name": r["original_filename"]})
        if len(batch) == 64:
            flush()
    flush()

    clean = [r for r in rows if not r["conflict"]]
    spoiled = {"rotten"}
    result = {
        "disclaimer": "Public-dataset benchmark on AgriFreshNET. "
                      + ("Held-out test split." if args.split == "test" else
                         "This split also selected the checkpoint, so it is optimistic.")
                      + " Not Instamart accuracy.",
        "model": str(args.model), "split": args.split, "classes": classes,
        "conflicting_groups_in_manifest": len(conflicting),
        "conflicting_rows_in_split": len(rows) - len(clean),
        "conflicting_filenames": conflict_files,
        "all_rows": summarize(rows, classes),
        "excluding_conflicting_groups": summarize(clean, classes),
        "low_confidence_rate_clean": round(sum(r["confidence"] < args.review_threshold for r in clean) / max(1, len(clean)), 4),
        "spoiled_binary_clean": {
            "recall": round(sum(r["pred"] in spoiled for r in clean if r["truth"] in spoiled)
                            / max(1, sum(r["truth"] in spoiled for r in clean)), 4),
            "precision": round(sum(r["truth"] in spoiled for r in clean if r["pred"] in spoiled)
                               / max(1, sum(r["pred"] in spoiled for r in clean)), 4),
        },
    }
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "freshness_metrics.json").write_text(json.dumps(result, indent=2), encoding="utf-8")

    # Gallery: deterministic sample of correct predictions per class + all-error sample.
    img_dir = args.out / "images"
    img_dir.mkdir(exist_ok=True)
    picks: list[dict] = []
    for c in classes:
        picks += [r for r in clean if r["truth"] == c and r["pred"] == c][: args.gallery_per_class]
    picks += [r for r in clean if r["truth"] != r["pred"]][: args.gallery_errors]
    cards = []
    for r in picks:
        dst = img_dir / f"{r['truth']}__{r['path'].name}"
        shutil.copyfile(r["path"], dst)
        ok = r["truth"] == r["pred"]
        low = r["confidence"] < args.review_threshold
        cards.append(
            f'<figure class="{"ok" if ok else "bad"}"><img src="images/{html.escape(dst.name)}" loading="lazy">'
            f'<figcaption>source label: <b>{r["truth"]}</b><br>model: <b>{r["pred"]}</b> '
            f'({r["confidence"]:.0%}){" - review" if low else ""}<br>'
            f'<small>{html.escape(r["name"])}</small></figcaption></figure>')
    s = result["excluding_conflicting_groups"]
    table = "".join(f"<tr><td>{c}</td><td>{v['precision']}</td><td>{v['recall']}</td><td>{v['f1']}</td>"
                    f"<td>{v['support']}</td></tr>" for c, v in s["per_class"].items())
    (args.out / "freshness_gallery.html").write_text(f"""<!doctype html><meta charset="utf-8">
<title>Freshness benchmark</title><style>body{{font:14px system-ui;margin:24px;max-width:1100px}}
.g{{display:grid;grid-template-columns:repeat(auto-fill,180px);gap:12px}}figure{{margin:0;border:2px solid #4a4;
border-radius:8px;overflow:hidden}}figure.bad{{border-color:#c33}}img{{width:100%;height:150px;object-fit:cover}}
figcaption{{padding:6px;font-size:12px}}td,th{{padding:4px 10px;border-bottom:1px solid #ddd;text-align:left}}</style>
<h1>Produce freshness - public-dataset benchmark</h1>
<p><b>AgriFreshNET {args.split} split, not Instamart data.</b> {"Held out from training and checkpoint selection." if args.split == "test" else "This split also chose the best checkpoint, so these figures are optimistic."} {len(conflicting)} filename groups appear under conflicting labels
({result['conflicting_rows_in_split']} rows in this split) and are excluded below.</p>
<p>Clean n={s['n']}, accuracy {s['accuracy']}, low-confidence (&lt;{args.review_threshold}) rate
{result['low_confidence_rate_clean']}. Spoiled (rotten) recall {result['spoiled_binary_clean']['recall']},
precision {result['spoiled_binary_clean']['precision']}.</p>
<table><tr><th>class</th><th>precision</th><th>recall</th><th>F1</th><th>n</th></tr>{table}</table>
<h2>Examples (green = correct, red = error)</h2><div class="g">{''.join(cards)}</div>""", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("conflicting_groups_in_manifest", "conflicting_rows_in_split")}))
    print("clean accuracy", s["accuracy"], "| all-rows accuracy", result["all_rows"]["accuracy"])
    print(f"wrote {args.out / 'freshness_metrics.json'} and freshness_gallery.html")


if __name__ == "__main__":
    main()

