"""S3 API (boto3, SigV4, path-style). boto3 sinxron — chaqiruvlar thread pool'da (`asyncio.to_thread`).

Ikki client: `ops` — ichki manzil (HEAD/GET/PUT/DELETE), `presign` — public manzil (imzo hostni ham
o'z ichiga oladi, shuning uchun brauzer ko'radigan manzil bilan imzolanadi; tarmoqqa chiqmaydi).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from medbron_common.errors import ServiceUnavailable

from app.core.config import Settings, get_settings


class StorageUnavailable(ServiceUnavailable):
    code = "STORAGE_UNAVAILABLE"
    message = "Fayl ombori vaqtincha mavjud emas"


@dataclass(frozen=True)
class ObjectInfo:
    size: int
    content_type: str | None


def _client(settings: Settings, endpoint: str) -> Any:
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key.get_secret_value() if settings.s3_access_key else "local",
        aws_secret_access_key=settings.s3_secret_key.get_secret_value() if settings.s3_secret_key else "local",
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=3,
            read_timeout=20,
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


class Storage:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._ops = _client(settings, settings.s3_endpoint_url)
        self._presign = _client(settings, settings.s3_public_endpoint_url)

    def bucket(self, visibility: str) -> str:
        return self.settings.s3_public_bucket if visibility == "public" else self.settings.s3_private_bucket

    def public_url(self, key: str) -> str:
        return f"{self.settings.s3_public_endpoint_url.rstrip('/')}/{self.settings.s3_public_bucket}/{key}"

    def presign_put(self, bucket: str, key: str, content_type: str, ttl: int) -> str:
        # Content-Type imzoga kiradi: boshqa tur bilan yuklash 403 (ADR-0010 da tekshirilgan).
        url: str = self._presign.generate_presigned_url(
            "put_object", Params={"Bucket": bucket, "Key": key, "ContentType": content_type}, ExpiresIn=ttl
        )
        return url

    def presign_get(self, bucket: str, key: str, ttl: int, filename: str | None = None) -> str:
        params: dict[str, str] = {"Bucket": bucket, "Key": key}
        if filename:
            params["ResponseContentDisposition"] = f'inline; filename="{filename}"'
        url: str = self._presign.generate_presigned_url("get_object", Params=params, ExpiresIn=ttl)
        return url

    async def _call(self, fn: Any, **kwargs: Any) -> Any:
        try:
            return await asyncio.to_thread(fn, **kwargs)
        except ClientError:
            raise
        except BotoCoreError as exc:
            raise StorageUnavailable() from exc

    async def head(self, bucket: str, key: str) -> ObjectInfo | None:
        try:
            resp = await self._call(self._ops.head_object, Bucket=bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise StorageUnavailable() from exc
        return ObjectInfo(size=int(resp["ContentLength"]), content_type=resp.get("ContentType"))

    async def read(self, bucket: str, key: str, *, first_bytes: int | None = None) -> bytes:
        kwargs: dict[str, Any] = {"Bucket": bucket, "Key": key}
        if first_bytes:
            kwargs["Range"] = f"bytes=0-{first_bytes - 1}"
        try:
            resp = await self._call(self._ops.get_object, **kwargs)
            body: bytes = await asyncio.to_thread(resp["Body"].read)
        except ClientError as exc:
            raise StorageUnavailable() from exc
        return body

    async def put(self, bucket: str, key: str, data: bytes, content_type: str) -> None:
        try:
            await self._call(self._ops.put_object, Bucket=bucket, Key=key, Body=data, ContentType=content_type)
        except ClientError as exc:
            raise StorageUnavailable() from exc

    async def delete(self, bucket: str, key: str) -> None:
        try:
            await self._call(self._ops.delete_object, Bucket=bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in ("404", "NoSuchKey"):
                raise StorageUnavailable() from exc

    async def ping(self) -> None:
        try:
            await self._call(self._ops.head_bucket, Bucket=self.settings.s3_public_bucket)
        except ClientError as exc:
            raise StorageUnavailable() from exc


@lru_cache(maxsize=1)
def get_storage() -> Storage:
    return Storage(get_settings())
