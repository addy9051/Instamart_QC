import base64
import io
from pathlib import Path


class FreshnessClassifier:
    """TorchScript adapter. No model means safe review-required behavior."""

    def __init__(self, model_path: str | None = None, threshold: float = 0.80) -> None:
        self.threshold = threshold
        self.model = None
        self.torch = None
        self.transforms = None
        if model_path and Path(model_path).is_file():
            try:
                import torch
                from torchvision import transforms

                self.torch = torch
                self.model = torch.jit.load(model_path, map_location="cpu").eval()
                self.transforms = transforms.Compose([
                    transforms.Resize((224, 224)), transforms.ToTensor(),
                    transforms.Normalize((0.485, 0.456, 0.406), (0.229, 0.224, 0.225)),
                ])
            except (ImportError, RuntimeError, OSError):
                self.model = None

    @property
    def ready(self) -> bool:
        return self.model is not None

    def predict_base64(self, encoded: str) -> dict:
        if not self.ready:
            return {"decision": "review_required", "class_name": None, "confidence": None, "reason_codes": ["freshness_model_unavailable"], "model_ready": False}
        try:
            from PIL import Image

            image = Image.open(io.BytesIO(base64.b64decode(encoded, validate=True)))
            if image.width * image.height > 25_000_000:
                raise ValueError("image pixel limit exceeded")
            image = image.convert("RGB")
            tensor = self.transforms(image).unsqueeze(0)
            with self.torch.inference_mode():
                probabilities = self.model(tensor).softmax(dim=1)[0]
            score, index = probabilities.max(dim=0)
            class_name = ["fresh", "spoiled"][int(index)]
            confidence = float(score)
            if confidence < self.threshold:
                decision, reason = "review_required", "freshness_low_confidence"
            elif class_name == "spoiled":
                decision, reason = "reject", "freshness_spoilage_detected"
            else:
                decision, reason = "accept", "freshness_pass"
            return {"decision": decision, "class_name": class_name, "confidence": confidence, "reason_codes": [reason], "model_ready": True}
        except Exception:
            return {"decision": "review_required", "class_name": None, "confidence": None, "reason_codes": ["freshness_inference_error"], "model_ready": self.ready}
