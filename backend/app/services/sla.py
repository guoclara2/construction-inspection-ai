# SLA 时限解析：规则优先级严格按总纲 §4.4——检查项 > 项目级精确 > 项目级风险等级 > 组织级 > 内置兜底。
# 时限一旦写入工单即固化（快照冗余原则），后续修改规则不影响存量工单。
# P06：补 resolve_escalation（升级规则）与 resolve_urge_interval_hours（催办限流窗口）。
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from ..models import InspectionItem, Project, RectifyOrder, SlaRule

# 内置兜底：无任何规则命中时的默认时限（小时）
BUILTIN_SLA = {"high": 8, "medium": 48, "low": 168}

# 内置兜底：超期多久升级（小时）、升级给谁
BUILTIN_ESCALATE = {"high": 4, "medium": 24, "low": 48}
BUILTIN_ESCALATE_ROLE = "project_admin"

# 兜底临期提醒比例（规则未提供 warn_ratio 时使用）
BUILTIN_WARN_RATIO = Decimal("0.75")

# 兜底催办限流窗口（小时，规则未提供 urge_interval_hours 时使用）
BUILTIN_URGE_INTERVAL = 1


def _find_rule(
    db: Session,
    org_id: int,
    project_id: int,
    risk_level: str,
    defect_category: str | None,
) -> SlaRule | None:
    """按总纲 §4.4 优先级查规则：项目级精确（risk+category）> 项目级 risk > 组织级。
    （检查项 default_sla_hours 优先级由 resolve_sla_hours 单独处理，此处只查规则表）"""
    # 1. 项目级精确匹配（risk_level + defect_category）
    rule = (
        db.query(SlaRule)
        .filter(
            SlaRule.org_id == org_id,
            SlaRule.project_id == project_id,
            SlaRule.risk_level == risk_level,
            SlaRule.defect_category == defect_category,
            SlaRule.enabled.is_(True),
        )
        .first()
    )
    # 2. 项目级按风险等级
    if rule is None:
        rule = (
            db.query(SlaRule)
            .filter(
                SlaRule.org_id == org_id,
                SlaRule.project_id == project_id,
                SlaRule.risk_level == risk_level,
                SlaRule.defect_category.is_(None),
                SlaRule.enabled.is_(True),
            )
            .first()
        )
    # 3. 组织级默认
    if rule is None:
        rule = (
            db.query(SlaRule)
            .filter(
                SlaRule.org_id == org_id,
                SlaRule.project_id.is_(None),
                SlaRule.risk_level == risk_level,
                SlaRule.enabled.is_(True),
            )
            .first()
        )
    return rule


def resolve_sla_hours(
    item: InspectionItem | None,
    risk_level: str,
    defect_category: str | None,
    project_id: int,
    db: Session,
) -> tuple[int, SlaRule | None]:
    """解析本单整改时限：返回 (sla_hours, 命中的规则|None——None 表示来自检查项覆盖或内置兜底)"""
    # 1. 检查项 default_sla_hours（最高优先级）
    if item is not None and item.default_sla_hours is not None:
        return int(item.default_sla_hours), None

    project = db.get(Project, project_id)
    org_id = project.org_id if project else None
    if org_id is None:
        return BUILTIN_SLA.get(risk_level, BUILTIN_SLA["medium"]), None

    rule = _find_rule(db, org_id, project_id, risk_level, defect_category)
    if rule is not None:
        return int(rule.sla_hours), rule

    # 4. 内置兜底
    return BUILTIN_SLA.get(risk_level, BUILTIN_SLA["medium"]), None


def resolve_escalation(db: Session, order: RectifyOrder) -> tuple[int, str]:
    """解析工单升级规则（P06 §2.2）：返回 (escalate_after_hours, escalate_to_role)。
    规则按工单风险等级查（项目级 > 组织级 > 内置兜底）；时限快照未存升级参数，运行时解析。"""
    risk = order.risk_level or "medium"
    rule = _find_rule(db, order.org_id, order.project_id, risk, None)
    if rule is not None:
        return int(rule.escalate_after_hours), (rule.escalate_to_role or BUILTIN_ESCALATE_ROLE)
    return BUILTIN_ESCALATE.get(risk, BUILTIN_ESCALATE["medium"]), BUILTIN_ESCALATE_ROLE


def resolve_urge_interval_hours(db: Session, order: RectifyOrder) -> int:
    """解析催办限流窗口（P06 §2.3）：项目级规则 > 组织级规则 > 默认 1 小时。
    规则行未配置 urge_interval_hours（NULL）时继续向低优先级取，全空则用默认。"""
    risk = order.risk_level or "medium"
    rule = _find_rule(db, order.org_id, order.project_id, risk, None)
    if rule is not None and rule.urge_interval_hours is not None:
        return int(rule.urge_interval_hours)
    org_rule = (
        db.query(SlaRule)
        .filter(
            SlaRule.org_id == order.org_id,
            SlaRule.project_id.is_(None),
            SlaRule.risk_level == risk,
            SlaRule.enabled.is_(True),
            SlaRule.urge_interval_hours.isnot(None),
        )
        .first()
    )
    if org_rule is not None:
        return int(org_rule.urge_interval_hours)
    return BUILTIN_URGE_INTERVAL


def compute_deadline(created_at: datetime, sla_hours: int) -> datetime:
    """整改截止时间 = 创建时间 + 时限"""
    return created_at + timedelta(hours=sla_hours)


def compute_warn_at(created_at: datetime, sla_hours: int, warn_ratio: Decimal | float | None) -> datetime:
    """临期提醒时间 = 创建时间 + 时限 × 提醒比例（默认 0.75）"""
    ratio = Decimal(str(warn_ratio)) if warn_ratio is not None else BUILTIN_WARN_RATIO
    warn_seconds = float(timedelta(hours=sla_hours).total_seconds()) * float(ratio)
    return created_at + timedelta(seconds=warn_seconds)
