# 看板统计口径集中实现（总纲 §5.8：指标必须为本模块独立函数并有单元测试）。
# P06 先落地 sla_on_time_rate；其余指标随 P08/P10/P12 陆续迁入本模块。
from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session

from ..models import RectifyOrder


def sla_on_time_rate(db: Session, project_id: int) -> float:
    """按期完成率 = closed_at <= deadline 的闭环工单 / 全部闭环工单（百分数保留 1 位；分母为 0 → 0）"""
    total = (
        db.query(sa_func.count(RectifyOrder.id))
        .filter(RectifyOrder.project_id == project_id, RectifyOrder.status == "closed")
        .scalar()
    )
    if not total:
        return 0.0
    on_time = (
        db.query(sa_func.count(RectifyOrder.id))
        .filter(
            RectifyOrder.project_id == project_id,
            RectifyOrder.status == "closed",
            RectifyOrder.closed_at.isnot(None),
            RectifyOrder.closed_at <= RectifyOrder.deadline,
        )
        .scalar()
    )
    return round(on_time / total * 100, 1)
