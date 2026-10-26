"""Process-wide services, built once in the app lifespan and shared by every request.

Holding them in one object (on ``app.state``) instead of module globals keeps the app
factory re-entrant and lets tests build an isolated container per app.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from redis.asyncio import Redis

from smartrouter.core.aws import aws_client
from smartrouter.core.cache import ConfigCache
from smartrouter.core.config import Settings
from smartrouter.core.db import Database, import_all_models
from smartrouter.core.redis_client import create_redis
from smartrouter.core.sealing import KeySealer, KmsSealer, LocalSealer
from smartrouter.core.storage import LocalStorage, ObjectStorage, S3Storage
from smartrouter.providers.adapters.http import build_client
from smartrouter.providers.adapters.registry import AdapterRegistry


@dataclass
class Container:
    settings: Settings
    db: Database
    redis: Redis
    http: httpx.AsyncClient
    config_cache: ConfigCache
    adapters: AdapterRegistry
    sealer: KeySealer
    storage: ObjectStorage

    @classmethod
    def build(cls, settings: Settings) -> Container:
        import_all_models()  # complete ORM mapping for every entry point (API, worker, CLI)
        redis = create_redis(settings.redis_url)
        http = build_client()
        sealer: KeySealer = (
            KmsSealer(aws_client(settings, "kms"), settings.kms_key_id)
            if settings.key_mode == "kms"
            else LocalSealer(settings.local_master_key)
        )
        storage: ObjectStorage = (
            S3Storage(
                aws_client(settings, "s3"),
                settings.exports_bucket,
                aws_client(settings, "s3", public=True),
            )
            if settings.storage == "s3"
            else LocalStorage(settings.local_storage_dir)
        )
        return cls(
            settings=settings,
            db=Database(settings.database_url),
            redis=redis,
            http=http,
            config_cache=ConfigCache(redis),
            adapters=AdapterRegistry(http),
            sealer=sealer,
            storage=storage,
        )

    async def close(self) -> None:
        await self.http.aclose()
        await self.redis.aclose()
        await self.db.dispose()
