"""Freshness model evaluation helpers (C17, C18, C20): checkpoint selection, calibration, and the
group-aware split used to avoid same-item leakage. Pure Python/NumPy so they are unit-testable."""
import math

import numpy as np


def fbeta(precision: float, recall: float, beta: float = 2.0) -> float:
    if precision <= 0 and recall <= 0:
        return 0.0
    b2 = beta * beta
    return (1 + b2) * precision * recall / max(1e-12, b2 * precision + recall)


def checkpoint_score(metric: str, precision: float, recall: float, min_precision: float = 0.9) -> float:
    """Higher is better. Recall alone is gamed by an 'always rotten' model (recall 1.0), so the
    default selects on F2 and the alternative selects on recall subject to a precision floor."""
    if metric == "recall":
        return recall
    if metric == "f2":
        return fbeta(precision, recall, 2.0)
    if metric == "recall_at_precision":
        # Any checkpoint meeting the floor beats every checkpoint that does not.
        return recall if precision >= min_precision else precision - 1.0
    raise ValueError(f"unknown selection metric: {metric!r}")


def softmax(logits: np.ndarray, temperature: float = 1.0) -> np.ndarray:
    scaled = np.asarray(logits, dtype=np.float64) / max(temperature, 1e-6)
    scaled = scaled - scaled.max(axis=-1, keepdims=True)
    exp = np.exp(scaled)
    return exp / exp.sum(axis=-1, keepdims=True)


def nll(logits: np.ndarray, labels: np.ndarray, temperature: float) -> float:
    probs = softmax(logits, temperature)
    picked = probs[np.arange(len(labels)), labels]
    return float(-np.mean(np.log(np.clip(picked, 1e-12, 1.0))))


def fit_temperature(logits: np.ndarray, labels: np.ndarray, low: float = 0.25, high: float = 8.0) -> float:
    """Temperature scaling: one scalar T minimising validation NLL (golden-section search)."""
    logits, labels = np.asarray(logits, dtype=np.float64), np.asarray(labels, dtype=int)
    if len(labels) == 0:
        return 1.0
    phi = (math.sqrt(5) - 1) / 2
    a, b = low, high
    c, d = b - phi * (b - a), a + phi * (b - a)
    for _ in range(60):
        if nll(logits, labels, c) < nll(logits, labels, d):
            b = d
        else:
            a = c
        c, d = b - phi * (b - a), a + phi * (b - a)
    return round((a + b) / 2, 4)


def expected_calibration_error(probs: np.ndarray, labels: np.ndarray, bins: int = 10) -> float:
    probs, labels = np.asarray(probs), np.asarray(labels, dtype=int)
    confidence = probs.max(axis=1)
    correct = probs.argmax(axis=1) == labels
    edges = np.linspace(0.0, 1.0, bins + 1)
    ece = 0.0
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        mask = (confidence > lo) & (confidence <= hi)
        if mask.any():
            ece += mask.mean() * abs(correct[mask].mean() - confidence[mask].mean())
    return float(ece)


def group_key(relative_path: str, pattern: str | None, fallback: str) -> str:
    """Group id for a split. `pattern` is a regex over the relative path whose first group names the
    physical item / capture sequence / session (e.g. r'(?:^|/)(\\w+_item\\d+)'); without a match the
    fallback (filename group) is used, which only keeps augmented copies together."""
    import re

    if pattern:
        match = re.search(pattern, relative_path)
        if match:
            return match.group(1) if match.groups() else match.group(0)
    return fallback


def commodity_of(relative_path: str, depth: int = 1, pattern: str | None = None) -> str:
    """Commodity name from a regex group or from the folder at `depth` below the stage folder."""
    import re

    if pattern:
        match = re.search(pattern, relative_path)
        if match:
            return (match.group(1) if match.groups() else match.group(0)).lower()
    parts = relative_path.replace("\\", "/").split("/")
    return parts[depth].lower() if len(parts) > depth + 1 else "unknown"
