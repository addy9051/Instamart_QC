"""Privacy controls for proof-of-pack imagery (I9, DPDP Act 2023).

Purpose limitation: images exist to settle order-completeness disputes. They are cropped to the
bag-mouth region, blurred where labels or faces may appear, downscaled, stripped of metadata,
and deleted after the retention window. Nothing here scores or ranks individual packers.
"""
import hashlib
import hmac
import io
from collections.abc import Callable
from dataclasses import dataclass, field

from PIL import Image, ImageFilter

Box = tuple[float, float, float, float]  # normalized (left, top, right, bottom) in the full frame

PURPOSE = "order_completeness_qc"
MAX_INPUT_PIXELS = 25_000_000


@dataclass(frozen=True)
class PrivacyPolicy:
    purpose: str = PURPOSE
    retention_days: int = 30
    roi: Box = (0.0, 0.0, 1.0, 1.0)
    # Fixed zones (e.g. where a printed customer label sits) blurred before anything is stored.
    blur_boxes: tuple[Box, ...] = field(default_factory=tuple)
    max_side_px: int = 960
    jpeg_quality: int = 80
    blur_radius: float = 24.0

    def __post_init__(self) -> None:
        for box in (self.roi, *self.blur_boxes):
            left, top, right, bottom = box
            if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
                raise ValueError(f"box must be normalized with left<right and top<bottom: {box}")
        if self.retention_days < 1:
            raise ValueError("retention_days must be at least 1")


FaceDetector = Callable[[Image.Image], list[Box]]


def _blur_region(image: Image.Image, box: Box, radius: float) -> None:
    width, height = image.size
    pixels = (int(box[0] * width), int(box[1] * height), int(box[2] * width), int(box[3] * height))
    if pixels[2] <= pixels[0] or pixels[3] <= pixels[1]:
        return
    region = image.crop(pixels).filter(ImageFilter.GaussianBlur(radius))
    image.paste(region, pixels[:2])


def redact_image(data: bytes, policy: PrivacyPolicy, face_detector: FaceDetector | None = None) -> bytes:
    """Return a redacted JPEG. Raises ValueError for undecodable or oversized images, so callers
    drop the frame instead of storing an unredacted one."""
    try:
        image = Image.open(io.BytesIO(data))
        if image.width * image.height > MAX_INPUT_PIXELS:
            raise ValueError("image is too large to process")
        image = image.convert("RGB")
    except (OSError, Image.DecompressionBombError) as error:
        raise ValueError("image could not be decoded") from error

    boxes = list(policy.blur_boxes)
    if face_detector is not None:
        boxes.extend(face_detector(image))
    for box in boxes:
        _blur_region(image, box, policy.blur_radius)

    width, height = image.size
    left, top, right, bottom = policy.roi
    image = image.crop((int(left * width), int(top * height), int(right * width), int(bottom * height)))
    image.thumbnail((policy.max_side_px, policy.max_side_px))

    out = io.BytesIO()
    image.save(out, format="JPEG", quality=policy.jpeg_quality)  # no exif= argument: metadata dropped
    return out.getvalue()


def parse_box(text: str) -> Box:
    values = tuple(float(part) for part in text.split(","))
    if len(values) != 4:
        raise ValueError(f"expected 'left,top,right,bottom', got {text!r}")
    return values  # type: ignore[return-value]


def parse_boxes(text: str) -> tuple[Box, ...]:
    return tuple(parse_box(part) for part in text.split(";") if part.strip())


def pseudonymize(identifier: str, salt: str) -> str:
    """Stable, non-reversible reference (e.g. for a station user) when one must be stored at all."""
    return hmac.new(salt.encode("utf-8"), identifier.encode("utf-8"), hashlib.sha256).hexdigest()[:12]
