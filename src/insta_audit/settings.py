from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Source-tree root (…/src/insta_audit/settings.py -> repo root). Only valid for editable/source installs.
_SOURCE_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INSTA_AUDIT_", env_file=".env", extra="ignore")

    environment: str = "development"
    api_key: str | None = None
    # Directory holding configs/, data/ and the demo UI files. Unset -> source tree if it has
    # configs/, else the current working directory (the Docker images use WORKDIR /app).
    project_root: str | None = None
    policy_path: str = "configs/quality_policy.yaml"
    sku_bank_path: str = "artifacts/sku_bank.npz"
    embedding_model_path: str | None = None
    freshness_model_path: str | None = None
    enable_ocr: bool = False
    ocr_det_model_dir: str | None = None
    ocr_rec_model_dir: str | None = None
    ocr_cls_model_dir: str | None = None
    min_visual_similarity: float = 0.5
    max_weight_error_pct: float = 18.0
    max_image_bytes: int = 5_000_000
    catalog_path: str = "data/catalog/skus.yaml"
    # Serve the browser demo files (index.html, app.js, css) even when environment=production.
    # This only controls static files; it does NOT disable API authentication.
    demo_ui: bool = False
    # Explicit opt-in to run production WITHOUT an API key (public showcase). Turns on per-client
    # rate limiting, a smaller image cap, and bounded inference concurrency.
    public_demo: bool = False
    public_demo_max_image_bytes: int = 1_500_000
    # Requests per minute per client on /v1/*. None -> 30 in public_demo mode, otherwise disabled (0).
    rate_limit_per_minute: int | None = None
    max_concurrent_inference: int = 2
    inference_wait_seconds: float = 10.0

    # Operating modes (I3). Everything starts in shadow; hard blocks need allow_enforce=true,
    # which should only be set after the per-order false-hold gate is met on real store data.
    modes_path: str = "configs/modes.yaml"
    default_mode: str | None = None
    kill_switch: bool = False
    allow_enforce: bool = False

    # Seal-weight check (I4). Calibrate scale_sigma_g from a known-weight test on the real scale.
    scale_resolution_g: float = 1.0
    scale_sigma_g: float = 2.0
    scale_k: float = 3.0
    seal_relative_tolerance_pct: float = 3.0
    nominal_weight_bias_pct: float = 10.0

    # Order data (I1). The mock source is for local pilots/tests; the webhook source needs a secret.
    mock_orders_path: str | None = None
    webhook_secret: str | None = None
    webhook_result_url: str | None = None
    webhook_order_ttl_seconds: int = 6 * 3600

    # Proof-of-pack (I2) and privacy (I9). Capture is opt-in per deployment.
    proof_enabled: bool = False
    proof_store: str = "local"  # local | s3
    proof_local_dir: str = "artifacts/proof"
    proof_s3_bucket: str | None = None
    proof_s3_prefix: str = ""
    proof_s3_endpoint_url: str | None = None
    proof_signing_key: str | None = None
    proof_retention_days: int = 30
    proof_max_images: int = 8
    # Normalized "left,top,right,bottom" of the bag mouth in the camera frame.
    proof_roi: str = "0,0,1,1"
    # Semicolon-separated normalized boxes blurred before storage (e.g. a printed-label zone).
    proof_blur_boxes: str = ""

    @property
    def root(self) -> Path:
        if self.project_root:
            return Path(self.project_root).resolve()
        if (_SOURCE_ROOT / "configs" / "quality_policy.yaml").is_file():
            return _SOURCE_ROOT
        return Path.cwd().resolve()

    def resolve(self, value: str | None) -> str | None:
        """Absolute paths pass through; relative paths resolve against the project root when that
        file/dir exists there, else stay as given (e.g. a Hugging Face model id)."""
        if not value:
            return value
        path = Path(value)
        if path.is_absolute():
            return str(path)
        candidate = self.root / path
        return str(candidate) if candidate.exists() else value

    @property
    def effective_rate_limit(self) -> int:
        if self.rate_limit_per_minute is not None:
            return max(0, self.rate_limit_per_minute)
        return 30 if self.public_demo else 0

    @property
    def effective_max_image_bytes(self) -> int:
        return min(self.max_image_bytes, self.public_demo_max_image_bytes) if self.public_demo else self.max_image_bytes


@lru_cache
def get_settings() -> Settings:
    return Settings()
