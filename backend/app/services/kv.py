# 短期键值存储：Redis 带 TTL；未配置 Redis 时降级为进程内带过期字典。
# 用途：微信绑定票据 bind_ticket（存 openid，TTL 10min）、短信验证码等临时凭据。
import logging
import threading
import time

from ..config import settings

logger = logging.getLogger(__name__)

_redis_client = None
_redis_tried = False
_local_lock = threading.Lock()
_local_store: dict[str, tuple[float, str]] = {}  # key -> (expire_epoch, value)


def _get_redis():
    global _redis_client, _redis_tried
    if _redis_tried:
        return _redis_client
    _redis_tried = True
    if not settings.REDIS_URL.strip():
        return None
    try:
        import redis

        _redis_client = redis.from_url(settings.REDIS_URL, socket_connect_timeout=1, decode_responses=True)
        _redis_client.ping()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Redis 连接失败，KV 降级为进程内实现: %s", exc)
        _redis_client = None
    return _redis_client


def set_kv(key: str, value: str, ttl_seconds: int) -> None:
    client = _get_redis()
    if client is not None:
        try:
            client.setex(f"kv:{key}", ttl_seconds, value)
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis setex 异常，降级进程内: %s", exc)
    with _local_lock:
        _local_store[key] = (time.time() + ttl_seconds, value)


def get_kv(key: str) -> str | None:
    client = _get_redis()
    if client is not None:
        try:
            return client.get(f"kv:{key}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis get 异常，降级进程内: %s", exc)
    with _local_lock:
        item = _local_store.get(key)
        if not item:
            return None
        expire, value = item
        if time.time() > expire:
            _local_store.pop(key, None)
            return None
        return value


def del_kv(key: str) -> None:
    client = _get_redis()
    if client is not None:
        try:
            client.delete(f"kv:{key}")
            return
        except Exception:  # noqa: BLE001
            pass
    with _local_lock:
        _local_store.pop(key, None)


def consume_kv(key: str, expected: str) -> bool:
    """Atomically compare and consume a login code; Redis failure must not authenticate."""
    client = _get_redis()
    if client is not None:
        return bool(client.eval("if redis.call('GET',KEYS[1]) == ARGV[1] then return redis.call('DEL',KEYS[1]) else return 0 end", 1, f"kv:{key}", expected))
    with _local_lock:
        item = _local_store.get(key)
        if not item or item[0] <= time.time() or item[1] != expected:
            return False
        _local_store.pop(key, None)
        return True
