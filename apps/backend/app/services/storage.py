"""对象存储封装：MinIO 优先（MINIO_ENDPOINT/ACCESS_KEY/SECRET_KEY 齐备即启用，bucket 缺省 xinhere），
未配置回退 apps/backend/data/artifacts 本地磁盘。上传/下载统一走本模块，与存储介质解耦。"""
from __future__ import annotations

import io
from pathlib import Path

from ..core.config import settings

_client = None


def _get_client():
    """惰性创建 MinIO 客户端（连接参数来自配置）；bucket 不存在则自动创建。"""
    global _client
    if _client is None:
        from minio import Minio

        _client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
            region=settings.minio_region,  # 服务端指定 region 时必传，否则 AuthorizationHeaderMalformed
        )
        if not _client.bucket_exists(settings.minio_bucket):
            _client.make_bucket(settings.minio_bucket)
    return _client


def _local_root() -> Path:
    base = Path(settings.artifact_dir) if settings.artifact_dir else (
        Path(__file__).resolve().parents[2] / "data" / "artifacts"
    )
    base.mkdir(parents=True, exist_ok=True)
    return base


def put(key: str, data: bytes, content_type: str | None = None) -> None:
    """写入对象：MinIO 启用走对象存储，否则写本地磁盘。"""
    if settings.minio_enabled:
        _get_client().put_object(
            settings.minio_bucket, key, io.BytesIO(data), len(data), content_type=content_type or "application/octet-stream"
        )
        return
    p = _local_root() / key
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def read(key: str) -> bytes:
    """读取对象字节：MinIO 启用从对象存储拉取，否则读本地磁盘。"""
    if settings.minio_enabled:
        resp = _get_client().get_object(settings.minio_bucket, key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()
    return (_local_root() / key).read_bytes()


def exists(key: str) -> bool:
    """对象是否已存在（幂等去重判断）。"""
    if settings.minio_enabled:
        from minio.error import S3Error

        try:
            _get_client().stat_object(settings.minio_bucket, key)
            return True
        except S3Error:
            return False
    return (_local_root() / key).exists()
