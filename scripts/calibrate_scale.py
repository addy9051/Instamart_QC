import argparse
from pathlib import Path

import yaml


def main() -> None:
    parser = argparse.ArgumentParser(description="Calculate a one-point load-cell scale coefficient")
    parser.add_argument("--zero-count", type=float, required=True, help="average raw count with the platform empty")
    parser.add_argument("--known-count", type=float, required=True, help="average raw count with known weight applied")
    parser.add_argument("--known-weight-g", type=float, required=True)
    parser.add_argument("--output", type=Path, default=Path("configs/load_cell_calibration.yaml"))
    args = parser.parse_args()
    difference = args.known_count - args.zero_count
    if difference == 0 or args.known_weight_g <= 0:
        raise SystemExit("known weight must be positive and raw counts must differ")
    calibration = {"grams_per_count": args.known_weight_g / difference, "zero_offset": args.zero_count, "known_weight_g": args.known_weight_g, "method": "single-point estimate; validate with multiple test weights"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(yaml.safe_dump(calibration, sort_keys=False), encoding="utf-8")
    print(f"Wrote calibration estimate to {args.output}; verify across the full operating range.")


if __name__ == "__main__":
    main()
