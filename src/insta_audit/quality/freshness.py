import base64
import io
import json
from pathlib import Path

from insta_audit.quality.freshness_policy import FreshnessPolicy, decide_freshness


class FreshnessClassifier:
    """TorchScript adapter. No model means safe review-required behavior.

    Probabilities are temperature-scaled with the value fitted on validation data at training time
    (stored in the checkpoint metadata), then mapped to an accept/discount/reject/review action by a
    cost-based policy instead of a single arbitrary softmax cut-off.
    """

    def __init__(self, model_path: str | None = None, policy: FreshnessPolicy | None = None) -> None:
        self.policy = policy or FreshnessPolicy()
        self.temperature = 1.0
        self.model = None
        self.torch = None
        self.transforms = None
        self.classes = ["fresh", "spoiled"]
        if model_path and Path(model_path).is_file():
            try:
                import torch
                from torchvision import transforms

                self.torch = torch
                self.model = torch.jit.load(model_path, map_location="cpu").eval()
                metadata_path = Path(model_path).with_suffix(Path(model_path).suffix + ".metadata.json")
                if metadata_path.is_file():
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                    self.classes = [self._normalize_class(name) for name in metadata.get("classes", self.classes)]
                    self.temperature = float(metadata.get("temperature") or 1.0)
                self.transforms = transforms.Compose([
                    transforms.Resize(256), transforms.CenterCrop(224), transforms.ToTensor(),
                    transforms.Normalize((0.485, 0.456, 0.406), (0.229, 0.224, 0.225)),
                ])
            except (ImportError, RuntimeError, OSError):
                self.model = None

    @property
    def ready(self) -> bool:
        return self.model is not None

    def predict_base64(self, encoded: str) -> dict:
        if not self.ready:
            return {"decision": "review_required", "action": "review", "class_name": None, "confidence": None,
                    "reason_codes": ["freshness_model_unavailable"], "model_ready": False}
        try:
            from PIL import Image

            image = Image.open(io.BytesIO(base64.b64decode(encoded, validate=True)))
            if image.width * image.height > 25_000_000:
                raise ValueError("image pixel limit exceeded")
            image = image.convert("RGB")
            tensor = self.transforms(image).unsqueeze(0)
            with self.torch.inference_mode():
                logits = self.model(tensor)[0]
            probabilities = (logits / self.temperature).softmax(dim=0).tolist()
            named = {self.classes[i] if i < len(self.classes) else f"class_{i}": p
                     for i, p in enumerate(probabilities)}
            class_name = max(named, key=named.get)
            outcome = decide_freshness(named, self.policy)
            return {"decision": outcome["decision"], "action": outcome["action"], "class_name": class_name,
                    "confidence": named[class_name], "probabilities": {k: round(v, 4) for k, v in named.items()},
                    "reason_codes": [outcome["reason"]], "model_ready": True}
        except Exception:
            return {"decision": "review_required", "action": "review", "class_name": None, "confidence": None,
                    "reason_codes": ["freshness_inference_error"], "model_ready": self.ready}

    @staticmethod
    def _normalize_class(name: str) -> str:
        return name.strip().lower().replace("-", "_").replace(" ", "_")
