# 通用工具：统一响应、业务异常、用户序列化、JSON 读写
import json
import logging

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .. import models
from .timez import as_utc, fmt, now_utc, to_local  # noqa: F401  对外统一从 app.utils 暴露时间工具
from .trace import get_trace_id  # noqa: F401

logger = logging.getLogger(__name__)


def dumps(obj) -> str:
    """统一 JSON 序列化（中文不转义）"""
    return json.dumps(obj, ensure_ascii=False)


def loads_safe(s: str | None, default=None):
    """安全 JSON 反序列化：失败返回 default"""
    if not s:
        return default
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return default


def ok(data=None):
    """统一成功响应"""
    return {"code": 0, "msg": "ok", "data": data}


class BizError(Exception):
    """业务异常：HTTP 200 + code≠0"""

    def __init__(self, code: int, msg: str):
        self.code = code
        self.msg = msg
        super().__init__(msg)


def forbidden(msg: str = "无权限") -> HTTPException:
    """权限不足（P2-2 目标契约）：统一返回 HTTP 403 包裹体 {code:403, msg, data}。
    与 deps.py project_scope 越权 403 同风格；防枚举场景仍走 BizError(1020)。"""
    return HTTPException(status_code=403, detail={"code": 403, "msg": msg, "data": None})


# 常见 Pydantic 校验错误类型 → 中文（P1-2）；未命中且非自定义校验时原样输出英文 msg
_VALIDATION_TYPE_ZH = {
    "missing": "必填",
    "string_too_short": "长度过短",
    "string_too_long": "长度超限",
    "string_pattern_mismatch": "格式不正确",
    "string_type": "应为字符串",
    "enum": "取值不在允许范围内",
    "int_parsing": "应为整数",
    "int_type": "应为整数",
    "float_parsing": "应为数字",
    "bool_parsing": "应为布尔值",
    "greater_than_equal": "小于最小允许值",
    "less_than_equal": "大于最大允许值",
    "json_invalid": "JSON 格式错误",
}


def _fmt_validation_error(exc: RequestValidationError) -> str:
    """字段级校验错误中文化（P1-2）：拼接为「参数错误：字段 <路径> <原因>（当前值：…）」。
    自定义 field_validator（type=value_error）的 msg 已是中文，仅去掉 pydantic 英文前缀。"""
    parts = []
    for err in exc.errors()[:3]:  # 最多列 3 条，防 msg 超长
        loc = [str(x) for x in err.get("loc", []) if x not in ("body", "query", "path")]
        field = ".".join(loc) or "(未知字段)"
        etype = err.get("type", "")
        msg = err.get("msg", "")
        if etype in _VALIDATION_TYPE_ZH:
            reason = _VALIDATION_TYPE_ZH[etype]
        elif etype == "value_error":
            reason = msg.replace("Value error, ", "", 1) or msg
        else:
            reason = msg
        part = f"字段 {field} {reason}"
        cur = err.get("input")
        if cur is not None:
            if isinstance(cur, str):
                text = cur
            else:
                try:
                    text = dumps(cur)
                except (TypeError, ValueError):
                    text = str(cur)
            text = text.replace("\n", " ").replace("\r", " ")
            if len(text) > 50:
                text = text[:50] + "…"
            part += f"（当前值：{text}）"
        parts.append(part)
    return "参数错误：" + "；".join(parts)


def register_exception_handlers(app):
    @app.exception_handler(BizError)
    async def biz_error_handler(request: Request, exc: BizError):
        return JSONResponse(status_code=200, content={"code": exc.code, "msg": exc.msg, "data": None})

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=200, content={"code": 1000, "msg": _fmt_validation_error(exc), "data": None})

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception):
        # 未预期异常：HTTP 500 + trace_id（对「一律 200」的第一步修正，其余状态码在 P13 统一）
        trace_id = get_trace_id()
        logger.exception("未预期异常 trace_id=%s %s %s", trace_id, request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"code": 500, "msg": "服务异常", "data": None, "trace_id": trace_id},
        )


def user_to_dict(user: "models.User", project_name: str | None = None) -> dict:
    """user 对象序列化契约（role/specialty/project 归属改由 project_members 承载，见登录响应 projects/roles）"""
    return {
        "id": user.id,
        "username": user.username,
        "name": user.name,
        "phone": user.phone,
        "is_org_admin": user.is_org_admin,
        "project_name": project_name,
    }
