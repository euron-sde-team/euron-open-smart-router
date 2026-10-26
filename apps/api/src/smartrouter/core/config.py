"""Runtime configuration, validated once at boot.

Every setting comes from the environment. Security-relevant settings are checked by
``Settings.guard`` so a misconfigured production deployment refuses to start instead of
running with demo secrets.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

if TYPE_CHECKING:
    from smartrouter.health.tracker import BreakerConfig

INSECURE_SECRETS = {"", "change-me", "replace-with-output-of-make-env", "dev", "secret"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    environment: Literal["development", "test", "production"] = "development"
    cloud: Literal["local", "floci", "aws"] = Field("local", alias="SMARTROUTER_CLOUD")
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://smartrouter:smartrouter@localhost:4242/smartrouter"
    redis_url: str = "redis://localhost:4279/0"

    # Signing secret for dashboard sessions and pepper for API key hashes. Separate values so
    # rotating sessions does not invalidate every gateway key.
    session_secret: str = "dev-session-secret-not-for-production"  # noqa: S105 dev default, refused in production by guard()
    api_key_pepper: str = "dev-api-key-pepper-not-for-production"
    session_ttl_hours: int = 12
    cookie_secure: bool = False
    # NoDecode: read the env value as a plain comma-separated string, not JSON.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:4210"]
    )

    # Provider credentials are sealed before they reach the database.
    # local: AES-GCM with LOCAL_MASTER_KEY. kms: AWS KMS (Floci locally).
    key_mode: Literal["local", "kms"] = "local"
    local_master_key: str = "dev-local-master-key-not-for-production"
    kms_key_id: str = "alias/smartrouter-local"

    aws_region: str = "us-east-1"
    aws_endpoint_url: str | None = None
    s3_force_path_style: bool = False
    s3_public_endpoint: str | None = None
    exports_bucket: str = "smartrouter-local-exports"
    storage: Literal["local", "s3"] = "local"
    local_storage_dir: str = "./.data/exports"

    # Gateway behaviour
    upstream_timeout_s: float = 60.0
    default_max_output_tokens: int = 1024
    # Circuit breaker: open after N consecutive failures, or an error rate above the
    # threshold over the 5-minute window with enough samples; probe again after cooldown.
    breaker_failure_threshold: int = 5
    breaker_error_rate: float = 0.5
    breaker_min_samples: int = 10
    breaker_cooldown_s: int = 30
    # How often the worker probes each endpoint's model listing.
    health_probe_interval_s: int = 15
    # Reverse proxies in front of the API that append X-Forwarded-For (the ALB on AWS, the
    # dashboard's Next.js proxy under Compose). 0: the direct peer is the client.
    trusted_proxy_hops: int = Field(0, ge=0, le=5)
    # Endpoints on loopback or private networks (local Ollama, vLLM in the VPC). Metadata and
    # link-local addresses are refused regardless (providers/urls.py).
    endpoint_allow_private: bool = True
    # A run still "running" after this long lost its worker (crash, redeploy): mark it failed.
    benchmark_stale_after_s: int = 7200
    # How long benchmark result files are kept (the storage module's expiration_days).
    benchmark_results_days: int = 365

    otel_enabled: bool = False

    @property
    def breaker(self) -> BreakerConfig:
        from smartrouter.health.tracker import BreakerConfig

        return BreakerConfig(
            failure_threshold=self.breaker_failure_threshold,
            error_rate_threshold=self.breaker_error_rate,
            min_samples=self.breaker_min_samples,
            cooldown_s=self.breaker_cooldown_s,
        )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    def guard(self) -> None:
        """Refuse to boot in production with demo secrets or unsafe options."""
        if self.environment != "production":
            return
        problems: list[str] = []
        for name in ("session_secret", "api_key_pepper"):
            value = getattr(self, name)
            if value in INSECURE_SECRETS or "not-for-production" in value or len(value) < 32:
                problems.append(f"{name.upper()} must be a random value of at least 32 chars")
        if self.key_mode == "local" and (
            "not-for-production" in self.local_master_key or len(self.local_master_key) < 32
        ):
            problems.append("LOCAL_MASTER_KEY must be set (or use KEY_MODE=kms)")
        if self.session_secret == self.api_key_pepper:
            problems.append("SESSION_SECRET and API_KEY_PEPPER must differ")
        if self.cloud == "aws" and self.aws_endpoint_url:
            problems.append("AWS_ENDPOINT_URL must be unset when SMARTROUTER_CLOUD=aws")
        if problems:
            raise RuntimeError("unsafe production configuration: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.guard()
    return settings
