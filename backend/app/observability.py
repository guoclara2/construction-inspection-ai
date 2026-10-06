# 可观测性：结构化 JSON 日志 + trace_id 中间件 + 访问日志（消灭 J7）
import json
import logging
import time
import uuid

from .config import settings
from .utils.trace import get_project_id, get_trace_id, get_user_id, set_project_id, set_trace_id, set_user_id

access_logger = logging.getLogger("app.access")


class JsonFormatter(logging.Formatter):
    """JSON 结构化日志格式：便于线上采集与按 trace_id 检索"""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "trace_id": getattr(record, "trace_id", None),
            "user_id": getattr(record, "user_id", None),
            "project_id": getattr(record, "project_id", None),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k in ("method", "path", "status", "latency_ms"):
            if hasattr(record, k):
                payload[k] = getattr(record, k)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class _ContextFilter(logging.Filter):
    """把当前请求的 trace_id/user_id/project_id 注入每条日志记录"""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "trace_id"):
            record.trace_id = get_trace_id()
        if not hasattr(record, "user_id"):
            record.user_id = get_user_id()
        if not hasattr(record, "project_id"):
            record.project_id = get_project_id()
        return True


def setup_logging() -> None:
    """按 LOG_FORMAT 配置根日志：json（生产采集）或 text（本地可读）"""
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler()
    handler.addFilter(_ContextFilter())
    if settings.LOG_FORMAT.lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [trace=%(trace_id)s user=%(user_id)s proj=%(project_id)s] %(name)s: %(message)s"
        ))
    root.addHandler(handler)
    root.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))


class TraceMiddleware:
    """纯 ASGI 中间件：生成 trace_id → contextvar + 响应头 X-Trace-Id，并输出访问日志。

    访问日志只记录方法/路径/状态/耗时，绝不记录请求体（密码、令牌、base64 图片）。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        trace_id = uuid.uuid4().hex[:12]
        set_trace_id(trace_id)
        set_user_id(None)
        set_project_id(None)
        start = time.perf_counter()
        status_holder = {"code": 500}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["code"] = message["status"]
                headers = list(message.get("headers") or [])
                headers.append((b"x-trace-id", trace_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            latency_ms = round((time.perf_counter() - start) * 1000, 1)
            access_logger.info(
                "access",
                extra={
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "status": status_holder["code"],
                    "latency_ms": latency_ms,
                    "trace_id": trace_id,
                },
            )
