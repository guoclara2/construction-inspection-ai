# 微信云托管对象存储适配（部署形态：微信云托管）。
# 小程序端经 wx.cloud.uploadFile 直传对象存储得到 fileID（cloud://环境ID.桶名/路径），
# 服务端经微信开放接口读写对象：
#   - 上传：/tcb/uploadfile 拿上传链接 + COS POST（两步，保证对象元数据完整，小程序端可经 fileID 访问）
#   - 下载/签名：/tcb/batchdownloadfile 换取短期 https 下载链接（图片展示/预览用）
#   - exists/delete：云托管内网免鉴权接口 /_/cos/getauth 取 COS 临时密钥 + boto3（S3 兼容）
# 微信接口鉴权两种模式（WX_CLOUD_CALL）：
#   - 云托管「开放接口服务」：容器内 http://api.weixin.qq.com 免 access_token（推荐，控制台开启并配置接口权限）
#   - 经典模式：WX_APPID/WX_SECRET 置换 access_token（本地开发/未开启开放接口服务时）
# 本后端仅在 STORAGE_BACKEND=wxcloud 时启用；本地开发仍走 local。
import logging
import threading
import time

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

_API_BASE = "https://api.weixin.qq.com"
# COS 临时密钥有效期内的客户端缓存（exists/delete 低频操作）
_COS_TTL = 600


def _wx_api(path: str, json_body: dict) -> dict:
    """调微信服务端接口：云托管开放接口服务走 HTTP 免鉴权；否则 HTTPS + access_token"""
    if settings.WX_CLOUD_CALL:
        resp = httpx.post(f"http://api.weixin.qq.com{path}", json=json_body, timeout=15)
    else:
        from .channels.wechat import get_access_token

        token = get_access_token()
        if not token:
            raise RuntimeError("微信 access_token 不可用：请检查 WX_APPID/WX_SECRET 配置")
        resp = httpx.post(f"{_API_BASE}{path}", params={"access_token": token},
                          json=json_body, timeout=15)
    return resp.json()


class WxCloudStorage:
    """微信云托管对象存储（COS）适配器。key 即对象存储 cloudPath；
    fileID = cloud://{WX_ENV_ID}.{WX_COS_BUCKET}/{key}。"""

    def __init__(self):
        if not settings.WX_ENV_ID or not settings.WX_COS_BUCKET:
            raise RuntimeError("STORAGE_BACKEND=wxcloud 需要 WX_ENV_ID 与 WX_COS_BUCKET 配置")
        self._dl_cache: dict[str, tuple[str, float]] = {}   # key -> (url, 过期时间戳)
        self._exists_cache: dict[str, float] = {}           # key -> 过期时间戳（只缓存 True）
        self._lock = threading.Lock()
        self._cos_client = None
        self._cos_expire = 0.0

    def file_id(self, key: str) -> str:
        return f"cloud://{settings.WX_ENV_ID}.{settings.WX_COS_BUCKET}/{key}"

    def key_of(self, file_id: str) -> str:
        """从 fileID 解析对象键：必须属于本环境本桶，防跨环境引用（非法格式抛 ValueError）"""
        prefix = f"cloud://{settings.WX_ENV_ID}.{settings.WX_COS_BUCKET}/"
        if not file_id.startswith(prefix) or "/" not in file_id[len(prefix):]:
            raise ValueError("非法的云存储文件 ID")
        key = file_id[len(prefix):]
        if not key or ".." in key or key.startswith("/"):
            raise ValueError("非法的云存储对象键")
        return key

    # ---------- 上传（两步：/tcb/uploadfile → POST COS）----------

    def _upload_meta(self, cloud_path: str) -> dict:
        data = _wx_api("/tcb/uploadfile", {"env": settings.WX_ENV_ID, "path": cloud_path})
        if data.get("errcode", 0) != 0:
            raise RuntimeError(f"获取云存储上传链接失败: {data}")
        return data

    def put(self, key: str, data, content_type: str) -> None:
        meta = self._upload_meta(key)
        form = {
            "key": key,
            "Signature": meta["authorization"],
            "x-cos-security-token": meta["token"],
            "x-cos-meta-fileid": meta["cos_file_id"],
        }
        name = key.rsplit("/", 1)[-1]
        r = httpx.post(
            meta["url"], data=form,
            files={"file": (name, data, content_type)},
            timeout=60,
        )
        if r.status_code not in (200, 204):
            raise RuntimeError(f"云存储上传失败: HTTP {r.status_code} {r.text[:200]}")
        self._exists_cache[key] = time.time() + _COS_TTL
        with self._lock:
            self._dl_cache.pop(key, None)

    def put_file(self, key: str, src_path: str, content_type: str) -> None:
        with open(src_path, "rb") as f:
            self.put(key, f, content_type)

    # ---------- 下载链接（/tcb/batchdownloadfile，短期 https）----------

    def _download_url(self, key: str, max_age: int) -> str:
        with self._lock:
            hit = self._dl_cache.get(key)
            if hit and hit[1] > time.time() + 60:
                return hit[0]
        data = _wx_api("/tcb/batchdownloadfile", {
            "env": settings.WX_ENV_ID,
            "file_list": [{"fileid": self.file_id(key), "max_age": max_age}],
        })
        if data.get("errcode", 0) != 0:
            raise RuntimeError(f"获取云存储下载链接失败: {data}")
        flist = data.get("file_list") or []
        url = (flist[0] or {}).get("download_url") if flist else None
        if not url:
            raise RuntimeError(f"云存储未返回下载链接: {data}")
        with self._lock:
            self._dl_cache[key] = (url, time.time() + max_age)
        return url

    def get(self, key: str) -> bytes:
        r = httpx.get(self._download_url(key, 300), timeout=60)
        r.raise_for_status()
        return r.content

    def open_stream(self, key: str):
        # 单文件 ≤ MAX_UPLOAD_MB，下载后按块产出（保持与 local 后端一致的流式接口）
        data = self.get(key)
        for i in range(0, len(data), 1024 * 256):
            yield data[i:i + 1024 * 256]

    def presign(self, key: str, ttl: int) -> str | None:
        expires = min(ttl, settings.SIGNED_URL_TTL_SECONDS)
        return self._download_url(key, expires)

    # ---------- exists / delete（COS 临时密钥 + boto3）----------

    def _cos(self):
        import boto3
        from botocore.config import Config

        if self._cos_client is not None and self._cos_expire > time.time() + 60:
            return self._cos_client
        # 云托管环境内网免鉴权接口取临时密钥（本地开发不可用，仅云托管部署时调用）
        r = httpx.get("http://api.weixin.qq.com/_/cos/getauth", timeout=10)
        auth = r.json()
        if not auth.get("TmpSecretId"):
            raise RuntimeError(f"获取 COS 临时密钥失败: {auth}")
        self._cos_client = boto3.client(
            "s3",
            endpoint_url=f"https://cos.{settings.WX_COS_REGION}.myqcloud.com",
            aws_access_key_id=auth["TmpSecretId"],
            aws_secret_access_key=auth["TmpSecretKey"],
            aws_session_token=auth.get("Token"),
            region_name=settings.WX_COS_REGION,
            config=Config(signature_version="s3v4"),
        )
        self._cos_expire = float(auth.get("ExpiredTime") or time.time() + _COS_TTL)
        return self._cos_client

    def exists(self, key: str) -> bool:
        hit = self._exists_cache.get(key)
        if hit and hit > time.time():
            return True
        from botocore.exceptions import ClientError

        try:
            self._cos().head_object(Bucket=settings.WX_COS_BUCKET, Key=key)
            self._exists_cache[key] = time.time() + _COS_TTL
            return True
        except ClientError:
            return False

    def delete(self, key: str) -> None:
        from botocore.exceptions import ClientError

        try:
            self._cos().delete_object(Bucket=settings.WX_COS_BUCKET, Key=key)
            self._exists_cache.pop(key, None)
            with self._lock:
                self._dl_cache.pop(key, None)
        except ClientError as exc:
            logger.warning("云存储删除对象失败 key=%s: %s", key, exc)

    # ---------- 供 from-cloud 上传通道使用 ----------

    def download_to_file(self, file_id: str, dest_path: str, max_bytes: int) -> int:
        """按 fileID 下载对象到本地文件（流式限额），返回字节数；超限抛 ValueError"""
        key = self.key_of(file_id)
        url = self._download_url(key, 300)
        total = 0
        with httpx.stream("GET", url, timeout=60) as r, open(dest_path, "wb") as out:
            r.raise_for_status()
            for chunk in r.iter_bytes(1024 * 256):
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"文件大小不能超过 {settings.MAX_UPLOAD_MB}MB")
                out.write(chunk)
        return total
