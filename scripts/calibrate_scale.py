"""Load-cell calibration. Prefer --points (multi-point, includes an empty-platform point); the
single-point form is only a rough starting estimate.

    python scripts/calibrate_scale.py --points "8120:0,10480:200,13990:500,20150:1000"
    python scripts/calibrate_scale.py --zero-count 8120 --known-count 13990 --known-weight-g 500
"""
import argparse
from pathlib import Path

import yaml

from insta_audit.sensors.load_cell import LoadCellCalibration


def parse_points(text: str) -> list[tuple[float, float]]:
    points = []
    for part in text.split(","):
        raw, grams = part.split(":")
        points.append((float(raw), float(grams)))
    return points


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--points", help="comma-separated raw_count:known_grams pairs (>= 2, include 0 g)")
    parser.add_argument("--zero-count", type=float, help="average raw count with the platform empty")
    parser.add_argument("--known-count", type=float, help="average raw count with known weight applied")
    parser.add_argument("--known-weight-g", type=float)
    parser.add_argument("--max-residual-g", type=float, default=1.0, help="fail if the fit is worse than this")
    parser.add_argument("--output", type=Path, default=Path("configs/load_cell_calibration.yaml"))
    args = parser.parse_args()

    if args.points:
        calibration = LoadCellCalibration.from_points(parse_points(args.points))
        if calibration.max_residual_g > args.max_residual_g:
            raise SystemExit(
                f"fit residual {calibration.max_residual_g:.2f} g exceeds {args.max_residual_g} g: "
                "check for a non-linear load cell, a loose platform, or a wrong known weight"
            )
        method = f"{calibration.points}-point least squares; re-check at shift start with a known weight"
    else:
        if args.zero_count is None or args.known_count is None or not args.known_weight_g:
            raise SystemExit("provide --points, or all of --zero-count, --known-count and --known-weight-g")
        if args.known_count == args.zero_count or args.known_weight_g <= 0:
            raise SystemExit("known weight must be positive and raw counts must differ")
        calibration = LoadCellCalibration(args.known_weight_g / (args.known_count - args.zero_count), args.zero_count)
        method = "single-point estimate; validate with multiple test weights"
    data = {
        "grams_per_count": calibration.grams_per_count,
        "zero_offset": calibration.zero_offset,
        "max_residual_g": round(calibration.max_residual_g, 3),
        "points": calibration.points,
        "method": method,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    print(f"Wrote calibration to {args.output} ({method}); max residual {calibration.max_residual_g:.2f} g.")


if __name__ == "__main__":
    main()
