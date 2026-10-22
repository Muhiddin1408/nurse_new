"""Sozlamalar: muhit o'zgaruvchilari (`.env.example` da hammasi izoh bilan)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr, model_validator

from medbron_common.config import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "file-service"

    # S3 API (ADR-0010). Ichki manzil — servis amallari uchun; public — presigned URL va ommaviy
    # rasm havolalari uchun (brauzer/ilova shu manzilga to'g'ridan-to'g'ri yuklaydi).
    s3_endpoint_url: str = "http://medbron-s3:8333"
    s3_public_endpoint_url: str = "http://localhost:9000"
    s3_region: str = "us-east-1"
    s3_access_key: SecretStr | None = None
    s3_secret_key: SecretStr | None = None
    s3_public_bucket: str = "public-media"
    s3_private_bucket: str = "private-documents"

    upload_url_ttl_seconds: int = 600
    # Maxfiy hujjat havolasi — ≤ 5 daqiqa (ARCHITECTURE 11)
    private_url_max_ttl_seconds: int = 300
    # complete/attach qilinmagan fayllar shuncha soatdan keyin o'chiriladi
    unattached_ttl_hours: int = 24
    max_pending_uploads_per_user: int = 20
    thumbnail_size: int = 256

    @model_validator(mode="after")
    def _s3_requirements(self) -> Settings:
        if self.environment not in ("local", "test") and (self.s3_access_key is None or self.s3_secret_key is None):
            raise ValueError("S3_ACCESS_KEY va S3_SECRET_KEY majburiy")
        if self.private_url_max_ttl_seconds > 300:
            raise ValueError("PRIVATE_URL_MAX_TTL_SECONDS 300 dan oshmasligi kerak")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
