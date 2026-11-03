import argparse
import json
from pathlib import Path

from insta_audit.vision.tracking import BagMouthTracker, Detection, PolygonROI


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract ByteTrack bag-mouth crossing events from a local video")
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("artifacts/drop_events.jsonl"))
    parser.add_argument("--roi", default="0,0.62;1,0.62;1,1;0,1",
                        help="bag-mouth polygon as normalized 'x,y;x,y;...' (>= 3 points)")
    parser.add_argument("--ignore-labels", default="hand,person,arm", help="detector classes never counted as items")
    parser.add_argument("--min-confidence", type=float, default=0.35)
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
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    roi = PolygonROI([(float(x) * width, float(y) * height) for x, y in (p.split(",") for p in args.roi.split(";"))])
    trigger = BagMouthTracker(
        roi, ignore_labels=tuple(args.ignore_labels.split(",")), min_confidence=args.min_confidence
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame_number = 0
    with args.output.open("w", encoding="utf-8") as output:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            frame_number += 1
            result = model.track(frame, persist=True, tracker="bytetrack.yaml", verbose=False)[0]
            detections = {}
            if result.boxes is not None and result.boxes.id is not None:
                boxes = result.boxes.xyxy.cpu().numpy()
                ids = result.boxes.id.int().cpu().tolist()
                classes = result.boxes.cls.int().cpu().tolist()
                scores = result.boxes.conf.cpu().tolist()
                for (x1, y1, x2, y2), track_id, cls, score in zip(boxes, ids, classes, scores, strict=True):
                    detections[track_id] = Detection((x1 + x2) / 2, (y1 + y2) / 2, str(result.names[cls]), score)
            # Both directions are written: pair them with settled scale steps (EventFuser) so hands,
            # ID switches and items taken back out are not counted as drops.
            for event in trigger.update(detections):
                output.write(json.dumps({"frame": frame_number, "track_id": event.track_id, "x": event.x,
                                         "y": event.y, "event": event.direction}) + "\n")
    capture.release()
    print(f"Wrote ROI crossing events to {args.output}; fuse with settled scale steps and order streams before scoring.")


if __name__ == "__main__":
    main()
