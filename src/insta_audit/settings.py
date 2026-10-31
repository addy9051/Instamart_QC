from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="INSTA_AUDIT_", env_file=".env", extra="ignore")

    environment: str = "development"
    api_key: str | None = None
    sku_bank_path: str = "artifacts/sku_bank.npz"
    embedding_model_path: str | None = None
    freshness_model_path: str | None = None
    enable_ocr: bool = False
    ocr_det_model_dir: str | None = None
    ocr_rec_model_dir: str | None = None
    ocr_cls_model_dir: str | None = None
    min_visual_similarity: float = 0.78
    max_weight_error_pct: float = 18.0
    max_image_bytes: int = 5_000_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
