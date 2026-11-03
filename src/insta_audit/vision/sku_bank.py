import json
from pathlib import Path

import numpy as np


class SkuBank:
    """Small exact-search cosine bank; replace with FAISS when the SKU count warrants it."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.skus: np.ndarray | None = None
        self.vectors: np.ndarray | None = None
        self.metadata: dict = {}
        if self.path.is_file():
            payload = np.load(self.path, allow_pickle=False)
            self.skus = payload["skus"].astype(str)
            self.vectors = self._normalize(payload["embeddings"].astype(np.float32))
            meta_path = self.path.with_suffix(self.path.suffix + ".metadata.json")
            if meta_path.is_file():
                try:
                    self.metadata = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    self.metadata = {}

    @staticmethod
    def _normalize(vectors: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
        return vectors / np.maximum(norms, 1e-12)

    @property
    def ready(self) -> bool:
        return self.skus is not None and self.vectors is not None and len(self.skus) > 0

    @property
    def vector_dim(self) -> int | None:
        if self.vectors is not None and len(self.vectors.shape) > 1:
            return int(self.vectors.shape[1])
        return self.metadata.get("embedding_dimension")

    def match(self, vector: list[float] | np.ndarray) -> tuple[str, float] | None:
        if not self.ready:
            return None
        query = self._normalize(np.asarray(vector, dtype=np.float32).reshape(1, -1))
        if query.shape[1] != self.vectors.shape[1]:
            raise ValueError(
                f"Embedding dimension mismatch: query {query.shape[1]} vs bank {self.vectors.shape[1]}"
            )
        scores = self.vectors @ query[0]
        index = int(scores.argmax())
        return str(self.skus[index]), float(scores[index])
