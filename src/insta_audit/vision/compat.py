"""Embedder/bank compatibility check (C10): a mismatched model silently turns every audit into a
review, so detect it at startup and surface it on /health."""
from pathlib import PurePosixPath


def _model_key(name: str | None) -> str | None:
    if not name:
        return None
    return PurePosixPath(str(name).replace("\\", "/").rstrip("/")).name.lower() or None


def check_compatibility(
    *,
    bank_ready: bool,
    bank_dim: int | None,
    bank_model: str | None,
    embedder_ready: bool,
    embedder_dim: int | None,
    embedder_model: str | None,
) -> dict:
    info = {"bank_model": bank_model, "embedder_model": embedder_model,
            "bank_dim": bank_dim, "embedder_dim": embedder_dim}
    if not (bank_ready and embedder_ready):
        return {"status": "unchecked", "reasons": [], **info}
    reasons: list[str] = []
    if embedder_dim and bank_dim and embedder_dim != bank_dim:
        reasons.append(f"dimension_mismatch: embedder {embedder_dim}-d vs bank {bank_dim}-d")
    bank_key, embedder_key = _model_key(bank_model), _model_key(embedder_model)
    if bank_key and embedder_key and bank_key != embedder_key:
        reasons.append(f"model_mismatch: embedder {embedder_model!r} vs bank built with {bank_model!r}")
    return {"status": "mismatch" if reasons else "ok", "reasons": reasons, **info}
