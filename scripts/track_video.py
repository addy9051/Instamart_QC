import argparse
import json
from pathlib import Path

from insta_audit.vision.tracking import CrossingLine


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract ByteTrack bag-mouth crossing events from a local video")
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("artifacts/drop_events.jsonl"))
    parser.add_argument("--line-y", type=float, default=0.62, help="normalized vertical crossing coordinate")
    args = parser.parse_args()
    try:
        import cv2
        from ultralytics import YOLO
    except ImportError as exc:
        raise SystemExit('Install optional training dependencies: pip install -e ".[training]"') from exc
    if not args.weights.is_file() or not args.video.is_file():
        raise SystemExit("weights and video must both exist")
    model = YOLO(str(args.weights))
    capture = cv2.VideoCapture(str(args.video))
    if not capture.isOpened():
        raise SystemExit(f"could not open video: {args.video}")
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    trigger = CrossingLine(y_threshold=height * args.line_y)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame_number = 0
    with args.output.open("w", encoding="utf-8") as output:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            frame_number += 1
            result = model.track(frame, persist=True, tracker="bytetrack.yaml", verbose=False)[0]
            centers = {}
            if result.boxes is not None and result.boxes.id is not None:
                boxes = result.boxes.xyxy.cpu().numpy()
                ids = result.boxes.id.int().cpu().tolist()
                centers = {track_id: ((x1 + x2) / 2, (y1 + y2) / 2) for (x1, y1, x2, y2), track_id in zip(boxes, ids)}
            for event in trigger.update(centers):
                if event.direction == "into_bag":
                    output.write(json.dumps({"frame": frame_number, "track_id": event.track_id, "x": event.x, "y": event.y, "event": event.direction}) + "\n")
    capture.release()
    print(f"Wrote line-crossing events to {args.output}; associate events with scale and order streams before scoring.")


if __name__ == "__main__":
    main()
