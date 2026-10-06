# 限流：Redis 固定窗口计数；未配置 Redis 时降级为进程内实现（总纲 §6.4）
import logging
import threading
import time

from ..config import settings

logger = logging.getLogger(__name__)

_redis_client = None
_redis_tried = False
_local_lock = threading.Lock()
# 进程内窗口：key -> (window_start_epoch, count)
_local_buckets: dict[str, tuple[float, int]] = {}


def _get_redis():
    global _redis_client, _redis_tried
    if _redis_tried:
        return _redis_client
    _redis_tried = True
    if not settings.REDIS_URL.strip():
        logger.warning("未配置 REDIS_URL，限流降级为进程内实现（多实例部署下不共享计数）")
        return None
    try:
        import redis

        _redis_client = redis.from_url(settings.REDIS_URL, socket_connect_timeout=1, decode_responses=True)
        _redis_client.ping()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis 连接失败，限流降级为进程内实现: %s", exc)
        _redis_client = None
    return _redis_client


def hit(key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    """记一次访问。返回 (allowed, retry_after_seconds)。超限时 allowed=False。"""
    client = _get_redis()
    if client is not None:
        try:
            full_key = f"rl:{key}"
            count = client.incr(full_key)
            if count == 1:
                client.expire(full_key, window_seconds)
            if count > limit:
                ttl = client.ttl(full_key)
                return False, ttl if ttl and ttl > 0 else window_seconds
            return True, 0
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis 限流异常，降级进程内: %s", exc)
    # 进程内降级
    now = time.time()
    with _local_lock:
        start, count = _local_buckets.get(key, (now, 0))
        if now - start >= window_seconds:
            start, count = now, 0
        count += 1
        _local_buckets[key] = (start, count)
        if count > limit:
            return False, int(window_seconds - (now - start)) + 1
        return True, 0


def get_remaining(key: str) -> int | None:
    """读取当前窗口计数（用于短信按天计数等场景），未知返回 None"""
    client = _get_redis()
    if client is not None:
        try:
            v = client.get(f"rl:{key}")
            return int(v) if v is not None else 0
        except Exception:  # noqa: BLE001
            return None
    with _local_lock:
        return _local_buckets.get(key, (0, 0))[1]
