# 请求级追踪上下文：trace_id / user_id / project_id 存入 contextvars，供日志与响应头关联
from contextvars import ContextVar

_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)
_user_id: ContextVar[int | None] = ContextVar("user_id", default=None)
_project_id: ContextVar[int | None] = ContextVar("project_id", default=None)


def set_trace_id(value: str | None) -> None:
    _trace_id.set(value)


def get_trace_id() -> str | None:
    return _trace_id.get()


def set_user_id(value: int | None) -> None:
    _user_id.set(value)


def get_user_id() -> int | None:
    return _user_id.get()


def set_project_id(value: int | None) -> None:
    _project_id.set(value)


def get_project_id() -> int | None:
    return _project_id.get()
