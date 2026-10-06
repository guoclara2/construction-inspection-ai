"""Every real gateway attempt consumes durable user + project quota, bounded by tenant token bucket."""
import logging
import math
import threading
import time
from contextvars import ContextVar
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from ..config import settings
from ..database import SessionLocal
from ..p1_models import AiBudget, AiUsage
from ..utils import now_utc, BizError

logger = logging.getLogger(__name__)

_scope = ContextVar('ai_scope', default=None)

def current_scope():
    return _scope.get()

@contextmanager
def ai_scope(project_id, user_id, org_id=None):
    token = _scope.set((project_id, user_id, org_id))
    try: yield
    finally: _scope.reset(token)

def reserve(purpose, model):
    scope = _scope.get()
    if scope is None:
        raise BizError(429, '模型调用缺少用户和项目计费上下文')
    project_id, user_id = scope[0], scope[1]
    from .insert_once import insert_once
    from sqlalchemy import update
    with SessionLocal() as db:
        for key, limit in ((f'project:{project_id}', settings.AI_DAILY_QUOTA),
                           (f'user:{user_id}', settings.AI_USER_DAILY_QUOTA)):
            day = now_utc().date()
            db.execute(insert_once(AiBudget.__table__, dict(key=key, day=day, calls=0), ['key', 'day'], db.bind.dialect.name))
            if db.execute(update(AiBudget).where(AiBudget.key == key, AiBudget.day == day,
                AiBudget.calls < limit).values(calls=AiBudget.calls+1)).rowcount != 1:
                raise BizError(429, '今日AI额度已用尽，请人工判断')
        # Serialised by the per-project budget row lock above.
        running = db.query(AiUsage).filter_by(project_id=project_id, status='running').filter(
            AiUsage.created_at > now_utc()-timedelta(minutes=5)).count()
        if running >= settings.AI_PROJECT_CONCURRENCY:
            raise BizError(429, '项目AI并发已达上限，请稍后重试')
        usage = AiUsage(project_id=project_id, user_id=user_id, purpose=purpose, model=model, status='running')
        db.add(usage); db.commit(); return usage.id

def finish(uid, response=None):
    with SessionLocal() as db:
        usage = db.get(AiUsage, uid)
        usage.status = 'success' if response is not None else 'failed'
        if response is not None and getattr(response, 'usage', None):
            usage.input_tokens = response.usage.prompt_tokens
            usage.output_tokens = response.usage.completion_tokens
            rates = settings.AI_PRICES.get(usage.model)
            if rates and usage.input_tokens is not None and usage.output_tokens is not None:
                usage.cost = str((Decimal(str(rates['input']))*usage.input_tokens +
                    Decimal(str(rates['output']))*usage.output_tokens)/1000000)
        db.commit()


# ---------- 租户级令牌桶限速（超限由 ai_client 降级 mock） ----------

_local_tb_lock = threading.Lock()
_local_tb: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_refill_epoch)

_TB_LUA = """
local key = KEYS[1]
local rate = tonumber(ARGV[1])
local burst = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local tokens = redis.call('HGET', key, 'tokens')
local ts = redis.call('HGET', key, 'ts')
if not tokens then
  tokens = burst
  ts = now
else
  tokens = math.min(burst, tokens + (now - ts) * rate)
end
if tokens >= 1 then
  redis.call('HSET', key, 'tokens', tokens - 1, 'ts', now)
  redis.call('EXPIRE', key, math.ceil(burst / rate) + 10)
  return {1, 0}
end
return {0, math.ceil((1 - tokens) / rate)}
"""

_redis_client = None
_redis_tried = False


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
        logger.warning("Redis 连接失败，AI 令牌桶降级为进程内实现: %s", exc)
        _redis_client = None
    return _redis_client


def _token_bucket(key: str, rate: float, burst: int) -> tuple[bool, int]:
    """令牌桶限速：返回 (allowed, retry_after_seconds)。Redis 原子脚本；未配置 Redis 降级进程内。"""
    client = _get_redis()
    if client is not None:
        try:
            allowed, wait = client.eval(_TB_LUA, 1, f"ai_tb:{key}", rate, burst, time.time())
            return bool(allowed), int(wait)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis 令牌桶异常，降级进程内: %s", exc)
    with _local_tb_lock:
        now = time.time()
        tokens, ts = _local_tb.get(key, (float(burst), now))
        tokens = min(float(burst), tokens + (now - ts) * rate)
        if tokens >= 1:
            _local_tb[key] = (tokens - 1, now)
            return True, 0
        return False, max(1, int(math.ceil((1 - tokens) / rate)))


def tenant_rate_allowed() -> tuple[bool, int]:
    """租户级令牌桶放行判断：无项目上下文时放行（不阻断计费校验路径）。
    租户键优先 org_id，回退 project_id。"""
    scope = _scope.get()
    if scope is None:
        return True, 0
    org_id = scope[2]
    key = f"org:{org_id}" if org_id is not None else f"project:{scope[0]}"
    return _token_bucket(key, settings.AI_TENANT_RATE, settings.AI_TENANT_BURST)
