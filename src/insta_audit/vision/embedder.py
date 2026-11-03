import base64
import io
import logging

logger = logging.getLogger("insta_audit.vision.embedder")


class DinoEmbedder:
    """Local-only DINOv2 embedder; downloads are an explicit setup step, never API startup behavior."""

    def __init__(self, model_path: str | None = None) -> None:
        self.processor = self.model = self.torch = None
        self.model_path = model_path
        self.dimension: int | None = None
        self.failures = 0
        if not model_path:
            return
        try:
            import torch
            from transformers import AutoImageProcessor, AutoModel

            self.torch = torch
            self.processor = AutoImageProcessor.from_pretrained(model_path, local_files_only=True)
            self.model = AutoModel.from_pretrained(model_path, local_files_only=True).eval()
            self.dimension = int(getattr(getattr(self.model, "config", None), "hidden_size", 384))
        except (ImportError, OSError, RuntimeError) as exc:
            logger.warning("Could not load embedding model from %s: %s", model_path, exc)
            self.processor = self.model = self.torch = None
            self.dimension = None

    @property
    def ready(self) -> bool:
        return self.model is not None

    def from_base64(self, encoded: str) -> list[float] | None:
        if not self.ready:
            return None
        try:
            from PIL import Image

            image = Image.open(io.BytesIO(base64.b64decode(encoded, validate=True)))
            if image.width * image.height > 25_000_000:
                logger.warning("Image pixel count exceeds 25M limit: %dx%d", image.width, image.height)
                return None
            image = image.convert("RGB")
            inputs = self.processor(images=image, return_tensors="pt")
            with self.torch.inference_mode():
                vector = self.model(**inputs).last_hidden_state[:, 0]
                vector = self.torch.nn.functional.normalize(vector, dim=1)[0]
            return vector.cpu().tolist()
        except Exception:
            self.failures += 1
            logger.exception("Failed to generate embedding from base64 image")
            return None
