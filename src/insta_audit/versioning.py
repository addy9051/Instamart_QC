"""Artifact fingerprints recorded on every decision (audit trail / proof-of-pack foundation)."""
import hashlib
from pathlib import Path

from insta_audit import __version__


def file_digest(path: str | Path | None, length: int = 12) -> str | None:
    """Short SHA-256 of a file, or of a directory's files (sorted), or None if missing."""
    if not path:
        return None
    path = Path(path)
    files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else []
    if not files:
        return None
    digest = hashlib.sha256()
    for file in files:
        if path.is_dir():
            digest.update(file.relative_to(path).as_posix().encode("utf-8"))
        with file.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                digest.update(chunk)
    return digest.hexdigest()[:length]


def build_versions(
    *,
    sku_bank_path: str | None,
    embedding_model_path: str | None,
    embedder_ready: bool,
    freshness_model_path: str | None,
    freshness_ready: bool,
    policy_path: str | Path,
    policy: dict,
    catalog_path: str | None,
    ocr_ready: bool,
) -> dict[str, str | None]:
    embedder = None
    if embedder_ready and embedding_model_path:
        config_digest = file_digest(Path(embedding_model_path) / "config.json") if Path(embedding_model_path).is_dir() else None
        embedder = f"{Path(embedding_model_path).name}@{config_digest}" if config_digest else Path(embedding_model_path).name
    policy_digest = file_digest(policy_path)
    return {
        "service": __version__,
        "sku_bank": file_digest(sku_bank_path),
        "sku_embedder": embedder,
        "freshness_model": file_digest(freshness_model_path) if freshness_ready else None,
        "quality_policy": f"v{policy.get('version', '?')}@{policy_digest}" if policy_digest else None,
        "catalog": file_digest(catalog_path),
        "expiry_ocr": "paddleocr" if ocr_ready else None,
    }

