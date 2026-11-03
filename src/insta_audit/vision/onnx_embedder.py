"""Optional ONNX Runtime embedder for edge devices (C24). Uses the graph written by
scripts/export_dino_onnx.py (input `pixel_values`, L2-normalised `embedding` output).

Preprocessing mirrors the Hugging Face DINOv2 image processor (resize shortest edge to 256 with
bicubic, centre-crop 224, ImageNet mean/std). Numerical parity with the torch embedder must still
be validated on the target device: `python scripts/check_onnx_parity.py`.
"""
import base64
import io
import logging

import numpy as np

logger = logging.getLogger("insta_audit.vision.onnx_embedder")

_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def preprocess(image, size: int = 224) -> np.ndarray:
    """PIL image -> float32 (1, 3, size, size)."""
    from PIL import Image

    image = image.convert("RGB")
    resize_to = round(size * 256 / 224)
    scale = resize_to / min(image.size)
    image = image.resize(
        (max(size, round(image.width * scale)), max(size, round(image.height * scale))), Image.BICUBIC
    )
    left, top = (image.width - size) // 2, (image.height - size) // 2
    image = image.crop((left, top, left + size, top + size))
    array = (np.asarray(image, dtype=np.float32) / 255.0 - _MEAN) / _STD
    return array.transpose(2, 0, 1)[None].astype(np.float32)


class OnnxEmbedder:
    def __init__(self, model_path: str | None = None, image_size: int = 224, session=None) -> None:
        self.model_path = model_path
        self.image_size = image_size
        self.session = session
        self.dimension: int | None = None
        self.failures = 0
        if self.session is None and model_path:
            try:
                import onnxruntime

                self.session = onnxruntime.InferenceSession(model_path, providers=["CPUExecutionProvider"])
            except (ImportError, OSError, RuntimeError, ValueError) as exc:
                logger.warning("Could not load ONNX embedder from %s: %s", model_path, exc)
        if self.session is not None:
            try:
                shape = self.session.get_outputs()[0].shape
                self.dimension = int(shape[-1]) if isinstance(shape[-1], int) else None
            except (AttributeError, IndexError, TypeError):
                self.dimension = None

    @property
    def ready(self) -> bool:
        return self.session is not None

    def from_base64(self, encoded: str) -> list[float] | None:
        if not self.ready:
            return None
        try:
            from PIL import Image

            image = Image.open(io.BytesIO(base64.b64decode(encoded, validate=True)))
            if image.width * image.height > 25_000_000:
                logger.warning("Image pixel count exceeds 25M limit: %dx%d", image.width, image.height)
                return None
            output = self.session.run(None, {"pixel_values": preprocess(image, self.image_size)})[0]
            vector = np.asarray(output, dtype=np.float32).reshape(-1)
            norm = float(np.linalg.norm(vector))
            if self.dimension is None:
                self.dimension = int(vector.shape[0])
            return (vector / max(norm, 1e-12)).tolist()
        except Exception:
            self.failures += 1
            logger.exception("ONNX embedding failed")
            return None
