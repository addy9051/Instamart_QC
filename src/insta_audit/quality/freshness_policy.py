"""Freshness decision policy (C18, C19): calibrated probabilities, cost-based thresholds, class->action
mapping, and an out-of-distribution / commodity gate based on DINOv2 prototypes."""
from dataclasses import dataclass

import numpy as np

ROTTEN = {"spoiled", "rotten"}


@dataclass(frozen=True)
class FreshnessPolicy:
    # Cost of accepting a rotten item vs rejecting a good one, relative to each other.
    cost_missed_rotten: float = 5.0
    cost_false_reject: float = 1.0
    accept_min_confidence: float = 0.80
    reject_min_probability: float = 0.60
    semi_fresh_min_probability: float = 0.50

    @property
    def review_rotten_probability(self) -> float:
        """Bayes threshold: below this P(rotten) accepting is cheaper than reviewing."""
        return self.cost_false_reject / (self.cost_false_reject + self.cost_missed_rotten)


def decide_freshness(probabilities: dict[str, float], policy: FreshnessPolicy) -> dict:
    """probabilities: calibrated class -> probability. Returns decision, action and reason."""
    p_rotten = sum(p for name, p in probabilities.items() if name in ROTTEN)
    p_fresh = probabilities.get("fresh", 0.0)
    p_semi = probabilities.get("semi_fresh", 0.0)
    if p_rotten >= policy.reject_min_probability:
        return {"decision": "reject", "action": "reject", "reason": "freshness_spoilage_detected"}
    if p_rotten >= policy.review_rotten_probability:
        return {"decision": "review_required", "action": "review", "reason": "freshness_possible_spoilage"}
    if p_fresh >= policy.accept_min_confidence:
        return {"decision": "accept", "action": "accept", "reason": "freshness_pass"}
    if p_semi >= policy.semi_fresh_min_probability:
        return {"decision": "hold", "action": "discount", "reason": "freshness_intermediate_grade"}
    return {"decision": "review_required", "action": "review", "reason": "freshness_low_confidence"}


class CommodityPrototypes:
    """Mean DINOv2 embedding per commodity, used to reject images that are not the requested
    produce at all (a chips packet must not get a fresh/rotten label)."""

    def __init__(self, commodities: list[str], vectors: np.ndarray) -> None:
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        self.commodities = [c.lower() for c in commodities]
        self.vectors = vectors / np.maximum(norms, 1e-12)

    @classmethod
    def load(cls, path: str | None) -> "CommodityPrototypes | None":
        if not path:
            return None
        try:
            payload = np.load(path, allow_pickle=False)
            return cls([str(c) for c in payload["commodities"]], payload["embeddings"].astype(np.float32))
        except (OSError, KeyError, ValueError):
            return None

    def check(self, embedding, requested: str | None, min_similarity: float, margin: float = 0.05) -> dict:
        query = np.asarray(embedding, dtype=np.float32)
        query = query / max(float(np.linalg.norm(query)), 1e-12)
        scores = self.vectors @ query
        order = np.argsort(-scores)
        best = self.commodities[int(order[0])]
        best_score = float(scores[order[0]])
        requested_key = (requested or "").lower()
        requested_score = None
        if requested_key in self.commodities:
            requested_score = float(scores[self.commodities.index(requested_key)])
        result = {"best_commodity": best, "best_similarity": round(best_score, 4),
                  "requested_similarity": round(requested_score, 4) if requested_score is not None else None,
                  "status": "ok", "reason": None}
        if best_score < min_similarity:
            result.update(status="out_of_distribution", reason="not_a_known_commodity")
        elif requested_score is not None:
            if requested_score < min_similarity or best_score - requested_score > margin:
                result.update(status="commodity_mismatch", reason="image_does_not_match_requested_commodity")
        return result
