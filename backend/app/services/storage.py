# 存储适配器：统一对象存储接口，本地磁盘（默认）与 S3/MinIO/OSS 两种后端（总纲 §2 / P04 §2.1）
# 对象一律通过鉴权接口或短期签名 URL 访问，file_key 不是可直接访问的 URL。
import logging
import os
import shutil
from typing import Iterator, Protocol

from ..config import settings

logger = logging.getLogger(__name__)

_CHUNK = 1024 * 256  # 流式读写块大小


class Storage(Protocol):
    """存储后端统一接口（P04 §2.1）"""

    def put(self, key: str, data: bytes, content_type: str) -> None: ...
    def put_file(self, key: str, src_path: str, content_type: str) -> None: ...
    def get(self, key: str) -> bytes: ...
    def open_stream(self, key: str) -> Iterator[bytes]: ...
    def presign(self, key: str, ttl: int) -> str | None: ...  # local 返回 None
    def delete(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...


class LocalStorage:
    """本地磁盘存储：根目录在 web 根之外（STORAGE_LOCAL_DIR），按对象键分目录落盘。"""

    def __init__(self, root: str):
        self.root = os.path.abspath(os.path.normpath(root))
        os.makedirs(self.root, exist_ok=True)

    def _path(self, key: str) -> str:
        # 防目录穿越：规范化后必须仍在 root 之内
        full = os.path.normpath(os.path.join(self.root, key.replace("/", os.sep)))
        if not (full == self.root or full.startswith(self.root + os.sep)):
            raise ValueError("非法对象键")
        return full

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)

    def put_file(self, key: str, src_path: str, content_type: str) -> None:
        path = self._path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        shutil.copyfile(src_path, path)

    def get(self, key: str) -> bytes:
        with open(self._path(key), "rb") as f:
            return f.read()

    def open_stream(self, key: str) -> Iterator[bytes]:
        with open(self._path(key), "rb") as f:
            while True:
                chunk = f.read(_CHUNK)
                if not chunk:
                    break
                yield chunk

    def presign(self, key: str, ttl: int) -> str | None:
        return None  # 本地存储不签名，走鉴权流式下载

    def delete(self, key: str) -> None:
        path = self._path(key)
        if os.path.exists(path):
            os.remove(path)

    def exists(self, key: str) -> bool:
        return os.path.exists(self._path(key))


class S3Storage:
    """S3 兼容存储（MinIO / 阿里云 OSS）：boto3 客户端，presign 返回短期签名 URL。"""

    def __init__(self):
        import boto3  # 延迟导入：未启用 s3 时不强依赖
        from botocore.config import Config

        self.bucket = settings.S3_BUCKET
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT or None,
            aws_access_key_id=settings.S3_AK or None,
            aws_secret_access_key=settings.S3_SK or None,
            region_name=settings.S3_REGION or None,
            config=Config(signature_version="s3v4"),
        )

    def put(self, key: str, data: bytes, content_type: str) -> None:
        # 显式 private：对象默认私有，不得依赖桶级公开读权限（消灭公开直连风险）
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type, ACL="private")

    def put_file(self, key: str, src_path: str, content_type: str) -> None:
        with open(src_path, "rb") as f:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=f, ContentType=content_type, ACL="private")

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def open_stream(self, key: str) -> Iterator[bytes]:
        body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"]
        while True:
            chunk = body.read(_CHUNK)
            if not chunk:
                break
            yield chunk

    def presign(self, key: str, ttl: int) -> str | None:
        # 签名 URL 有效期：不超过业务 ttl、配置 SIGNED_URL_TTL_SECONDS、硬上限 900s（15 分钟）
        expires = min(ttl, settings.SIGNED_URL_TTL_SECONDS, 900)
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires
        )

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False


_storage: Storage | None = None


def get_storage() -> Storage:
    """存储单例：按 STORAGE_BACKEND 选择后端（local 默认；wxcloud 为微信云托管对象存储）"""
    global _storage
    if _storage is None:
        backend = settings.STORAGE_BACKEND.strip().lower()
        if backend == "s3":
            _storage = S3Storage()
            logger.info("存储后端=s3 endpoint=%s bucket=%s", settings.S3_ENDPOINT, settings.S3_BUCKET)
        elif backend == "wxcloud":
            from .wxcloud import WxCloudStorage

            _storage = WxCloudStorage()
            logger.info("存储后端=wxcloud env=%s bucket=%s", settings.WX_ENV_ID, settings.WX_COS_BUCKET)
        else:
            _storage = LocalStorage(settings.storage_local_root)
            logger.info("存储后端=local root=%s", settings.storage_local_root)
    return _storage


def get_wxcloud_storage():
    """取微信云托管对象存储单例；当前后端不是 wxcloud 时返回 None"""
    from .wxcloud import WxCloudStorage

    st = get_storage()
    return st if isinstance(st, WxCloudStorage) else None
