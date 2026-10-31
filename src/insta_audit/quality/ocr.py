import base64
import io
from pathlib import Path


class ExpiryOCR:
    """Optional PaddleOCR 2.x adapter for close-up package date crops."""

    def __init__(self, det_dir: str | None, rec_dir: str | None, cls_dir: str | None) -> None:
        self.engine = None
        if not all((det_dir, rec_dir, cls_dir)) or not all(Path(p).is_dir() for p in (det_dir, rec_dir, cls_dir)):
            return
        try:
            from paddleocr import PaddleOCR

            self.engine = PaddleOCR(use_angle_cls=True, lang="en", show_log=False, det_model_dir=det_dir, rec_model_dir=rec_dir, cls_model_dir=cls_dir)
        except (ImportError, RuntimeError, OSError):
            self.engine = None

    @property
    def ready(self) -> bool:
        return self.engine is not None

    def recognize_base64(self, encoded: str) -> str | None:
        if not self.ready:
            return None
        try:
            import numpy as np
            from PIL import Image

            decoded = Image.open(io.BytesIO(base64.b64decode(encoded, validate=True)))
            if decoded.width * decoded.height > 25_000_000:
                return None
            image = np.asarray(decoded.convert("RGB"))
            result = self.engine.ocr(image, cls=True)
            lines = [line[1][0] for page in result if page for line in page if line and line[1]]
            return " ".join(lines) if lines else None
        except Exception:
            return None
