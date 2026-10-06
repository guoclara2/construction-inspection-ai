# 审计日志：安全敏感动作留痕（总纲 §4.9 / §6.1）。绝不记录密码、令牌、验证码明文。
import logging

from sqlalchemy.orm import Session

from ..models import AuditLog

logger = logging.getLogger(__name__)


def write_audit(
    db: Session,
    action: str,
    *,
    actor_id: int | None = None,
    target_type: str | None = None,
    target_id: str | int | None = None,
    result: str = "success",
    before: dict | None = None,
    after: dict | None = None,
    org_id: int | None = None,
    project_id: int | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
    commit: bool = True,
) -> None:
    """写一条审计日志。失败不阻断主流程（仅记 error 日志）。"""
    try:
        row = AuditLog(
            action=action,
            actor_id=actor_id,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            result=result,
            before=before,
            after=after,
            org_id=org_id,
            project_id=project_id,
            ip=ip,
            user_agent=(user_agent or "")[:300] or None,
        )
        db.add(row)
        if commit:
            db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.error("写审计日志失败 action=%s: %s", action, exc)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
