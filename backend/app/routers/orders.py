# 工单路由：列表 / 详情 / 状态操作 / 催办 / 指派选项（全程按 project_scope 强制隔离）
# P05（§2.6）：动作入参可选携带 expected_version（乐观锁，不匹配 → 1014）；序列化携带 version 供两端回传
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import ProjectScope, project_scope, require_perm
from ..models import Attachment, InspectionRecord, OrderLog, ProjectMember, RectifyOrder, User
from ..services.analyze import is_actionable_ai_result
from ..services.order_flow import cancel_order, log_urge, reassign_order, transition
from ..services.sla import resolve_urge_interval_hours
from ..utils import BizError, as_utc, fmt, forbidden, loads_safe, now_utc, ok
from .attachments import attachment_to_dict, load_bindable_attachment

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/orders", tags=["orders"])


class OrderActionIn(BaseModel):
    """无载荷动作（接收/开始/催办）通用入参：乐观锁版本，可选"""
    expected_version: int | None = None


def _fmt(dt: datetime | None) -> str | None:
    return fmt(dt)


def _is_overdue(order: RectifyOrder) -> bool:
    return order.status not in ("closed", "cancelled") and as_utc(order.deadline) < now_utc()


def order_to_dict(order: RectifyOrder, db: Session, detail: bool = False) -> dict:
    """工单序列化：列表精简 / 详情全量（均带 version 供提交动作时回传，P05 §2.6）"""
    record = db.get(InspectionRecord, order.record_id)
    assignee = db.get(User, order.assignee_id)
    inspector = db.get(User, order.inspector_id)
    data = {
        "id": order.id,
        "order_no": order.order_no,
        "project_id": order.project_id,
        "record_id": order.record_id,
        "item_name": record.item_name if record else None,
        "status": order.status,
        "round": order.round,
        "version": order.version,  # 乐观锁版本：提交动作时作为 expected_version 回传
        "overdue": _is_overdue(order),
        "assignee_id": order.assignee_id,
        "assignee_name": assignee.name if assignee else None,
        "inspector_id": order.inspector_id,
        "inspector_name": inspector.name if inspector else None,
        "created_at": _fmt(order.created_at),
        "deadline": _fmt(order.deadline),
    }
    if detail:
        overdue_hours = None
        if _is_overdue(order):
            overdue_hours = round((now_utc() - as_utc(order.deadline)).total_seconds() / 3600, 1)
        logs = (
            db.query(OrderLog).filter(OrderLog.order_id == order.id)
            .order_by(OrderLog.id.asc()).all()
        )
        log_list = []
        for lg in logs:
            op = db.get(User, lg.operator_id)
            log_list.append({
                "id": lg.id,
                "action": lg.action,
                "from_status": lg.from_status,
                "to_status": lg.to_status,
                "operator_name": op.name if op else None,
                "remark": lg.remark,
                "created_at": _fmt(lg.created_at),
            })
        # 本轮反馈附件（打回的历史轮次附件归档保留，当前反馈以最新 round 为准，P04 §2.5.4）
        feedback_photos = (
            db.query(Attachment)
            .filter(
                Attachment.biz_type == "order_feedback",
                Attachment.biz_id == order.id,
                Attachment.round == order.round,
                Attachment.deleted_at.is_(None),
            )
            .order_by(Attachment.sort_no, Attachment.id)
            .all()
        )
        record_photo = None
        if record is not None and record.primary_attachment_id is not None:
            att = db.get(Attachment, record.primary_attachment_id)
            if att is not None and att.deleted_at is None:
                record_photo = attachment_to_dict(att)
        from ..services.operations import feedback_history
        data["rounds"] = feedback_history(db, order)
        data.update({
            "problem_location": order.problem_location,
            "rectify_requirement": order.rectify_requirement,
            "basis": order.basis,
            "feedback_photos": [attachment_to_dict(a) for a in feedback_photos],
            "feedback_text": order.feedback_text,
            "review_result": order.review_result,
            "review_comment": order.review_comment,
            "closed_at": _fmt(order.closed_at),
            "overdue_hours": overdue_hours,
            "record": {
                "id": record.id,
                "item_name": record.item_name,
                "item_basis": record.item_basis,
                "defect_category": record.defect_category,
                "photo": record_photo,
                "ai_result": (loads_safe(record.ai_result) if is_actionable_ai_result(loads_safe(record.ai_result)) else dict(loads_safe(record.ai_result) or {}, has_defect=None, status="unknown")),
                "human_verdict": record.human_verdict,
            } if record else None,
            "logs": log_list,
        })
    return data


def _get_scoped_order(order_id: int, db: Session, scope: ProjectScope) -> RectifyOrder:
    """取工单并强制校验属于当前项目（不存在或跨项目一律 1020）"""
    order = db.get(RectifyOrder, order_id)
    if order is None or order.project_id != scope.project_id:
        raise BizError(1020, "工单不存在")
    return order


def _visible(order: RectifyOrder, scope: ProjectScope) -> bool:
    """工单可见性：责任人 / 发起人 / 可读全部角色"""
    return scope.can_read_all or scope.user.id in (order.assignee_id, order.inspector_id)


@router.get("")
def list_orders(
    view: str | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    scope: ProjectScope = Depends(project_scope),
):
    """工单列表：强制 project 过滤；view 默认按角色（rectifier→my/inspector→review/可读全部→all）"""
    if view not in ("my", "review", "mine", "all", None):
        raise BizError(1000, "view 参数不合法")
    roles = scope.effective_roles
    if view is None:
        if "rectifier" in roles:
            view = "my"
        elif "inspector" in roles:
            view = "review"
        elif scope.can_read_all:
            view = "all"
        else:
            view = "my"
    if view == "all" and not scope.can_read_all:
        raise forbidden("无权限")

    q = db.query(RectifyOrder).filter(RectifyOrder.project_id == scope.project_id)
    if view == "my":
        q = q.filter(RectifyOrder.assignee_id == scope.user.id)
    elif view == "mine":
        q = q.filter(RectifyOrder.inspector_id == scope.user.id)
    elif view == "review":
        # 待我审核：status=review 且我是发起人（reviewer 角色的审核池扩展留待 P09）
        q = q.filter(RectifyOrder.status == "review", RectifyOrder.assignee_id != scope.user.id)
        if "reviewer" not in roles:
            q = q.filter(RectifyOrder.inspector_id == scope.user.id)
    if status:
        q = q.filter(RectifyOrder.status == status)
    total = q.count()
    orders = q.order_by(RectifyOrder.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return ok({"list": [order_to_dict(o, db) for o in orders], "total": total, "page": page, "page_size": page_size})


@router.get("/assignee-options")
def assignee_options(db: Session = Depends(get_db), scope: ProjectScope = Depends(project_scope)):
    """指派选项：本项目启用的 rectifier 成员 + 未闭环工单数（inspector / 可读全部角色）"""
    if "inspector" not in scope.effective_roles and not scope.can_read_all:
        raise forbidden("无权限")
    rows = (
        db.query(User, ProjectMember)
        .join(ProjectMember, ProjectMember.user_id == User.id)
        .filter(
            ProjectMember.project_id == scope.project_id,
            ProjectMember.role == "rectifier",
            ProjectMember.active.is_(True),
            User.active.is_(True),
        )
        .order_by(User.id)
        .all()  # 结果集有界：本项目启用整改责任人（指派下拉，量级 < 数百）
    )
    result = []
    for u, m in rows:
        open_count = (
            db.query(RectifyOrder)
            .filter(
                RectifyOrder.assignee_id == u.id,
                RectifyOrder.project_id == scope.project_id,
                RectifyOrder.status != "closed",
            )
            .count()
        )
        result.append({"user_id": u.id, "name": u.name, "specialty": m.specialty,
                       "org_unit": m.org_unit, "open_count": open_count})
    return ok(result)


@router.get("/{order_id}")
def order_detail(order_id: int, db: Session = Depends(get_db), scope: ProjectScope = Depends(project_scope)):
    """工单详情：assignee / 发起人 / 可读全部角色可见"""
    order = _get_scoped_order(order_id, db, scope)
    if not _visible(order, scope):
        raise forbidden("无权限")
    return ok(order_to_dict(order, db, detail=True))


@router.post("/{order_id}/accept")
def accept_order(order_id: int, body: OrderActionIn | None = None, db: Session = Depends(get_db),
                 scope: ProjectScope = Depends(require_perm("order:accept"))):
    """责任人接收工单（可选 expected_version 乐观锁）"""
    order = _get_scoped_order(order_id, db, scope)
    if order.assignee_id != scope.user.id:
        raise forbidden("仅责任人可操作")
    transition(order, "accept", scope.user, db,
               expected_version=body.expected_version if body else None)
    return ok(order_to_dict(order, db, detail=True))


@router.post("/{order_id}/start")
def start_order(order_id: int, body: OrderActionIn | None = None, db: Session = Depends(get_db),
                scope: ProjectScope = Depends(require_perm("order:start"))):
    """责任人开始整改（可选 expected_version 乐观锁）"""
    order = _get_scoped_order(order_id, db, scope)
    if order.assignee_id != scope.user.id:
        raise forbidden("仅责任人可操作")
    transition(order, "start", scope.user, db,
               expected_version=body.expected_version if body else None)
    return ok(order_to_dict(order, db, detail=True))


class FeedbackIn(BaseModel):
    """整改反馈：照片为先上传的附件 id（必须现场拍摄，P04 §2.4）+ 乐观锁版本"""
    attachment_id: int
    text: str = Field(min_length=1, max_length=10000)
    attachment_ids: list[int] = Field(default_factory=list, max_length=8)
    expected_version: int | None = None

    @field_validator("text")
    @classmethod
    def valid_text(cls, v: str):
        if not v or not v.strip():
            raise ValueError("整改说明必填")
        return v.strip()


@router.post("/{order_id}/feedback")
def submit_feedback(order_id: int, body: FeedbackIn, db: Session = Depends(get_db),
                    scope: ProjectScope = Depends(require_perm("order:feedback"))):
    """责任人提交整改反馈（现场拍摄照片+说明均必填；非现场拍摄 → 1041）"""
    order = _get_scoped_order(order_id, db, scope)
    if order.assignee_id != scope.user.id:
        raise forbidden("仅责任人可操作")
    # 附件校验：本人上传、未绑定、biz_type=order_feedback 且 source=camera（1041）
    att = load_bindable_attachment(db, body.attachment_id, scope, "order_feedback")
    transition(order, "feedback", scope.user, db, payload={"attachment": att, "text": body.text, "attachments": [load_bindable_attachment(db, aid, scope, "order_feedback") for aid in set(body.attachment_ids) - {att.id}]},
               expected_version=body.expected_version)
    return ok(order_to_dict(order, db, detail=True))


class ReviewIn(BaseModel):
    result: str
    comment: str | None = Field(default=None, max_length=500)
    expected_version: int | None = None

    @field_validator("result")
    @classmethod
    def valid_result(cls, v: str):
        if v not in ("pass", "reject"):
            raise ValueError("result 只能为 pass/reject")
        return v


@router.post("/{order_id}/review")
def review_order(order_id: int, body: ReviewIn, db: Session = Depends(get_db),
                 scope: ProjectScope = Depends(require_perm("order:review"))):
    """审核：发起人本人或本项目 reviewer；pass 闭环 / reject 打回（comment 必填）"""
    order = _get_scoped_order(order_id, db, scope)
    # 发起人本人，或具备 reviewer 角色（审核人池的完整会签留待 P09）
    if order.inspector_id != scope.user.id and "reviewer" not in scope.effective_roles:
        raise forbidden("仅发起巡查员或复核人可审核")
    if body.result == "reject" and not (body.comment and body.comment.strip()):
        raise BizError(1000, "打回时审核意见必填")
    action = "review_pass" if body.result == "pass" else "review_reject"
    transition(order, action, scope.user, db, payload={"comment": body.comment},
               expected_version=body.expected_version)
    return ok(order_to_dict(order, db, detail=True))


@router.post("/{order_id}/urge")
def urge_order(order_id: int, body: OrderActionIn | None = None, db: Session = Depends(get_db),
               scope: ProjectScope = Depends(require_perm("order:urge"))):
    """催办：项目管理员 / 组织管理员；限流窗口由 SLA 规则配置（urge_interval_hours，默认 1 小时）"""
    order = _get_scoped_order(order_id, db, scope)
    if order.status == "closed":
        raise BizError(400, "工单已闭环，无需催办")
    if order.status == "cancelled":
        raise BizError(400, "工单已作废，无需催办")
    interval_hours = resolve_urge_interval_hours(db, order)
    last_urge = (
        db.query(OrderLog)
        .filter(OrderLog.order_id == order.id, OrderLog.action == "urge")
        .order_by(OrderLog.id.desc())
        .first()
    )
    if last_urge and last_urge.created_at and now_utc() - as_utc(last_urge.created_at) < timedelta(hours=interval_hours):
        raise BizError(429, f"该工单{interval_hours}小时内已催办，请稍后再试")
    log_urge(order, scope.user, db, expected_version=body.expected_version if body else None)
    return ok(order_to_dict(order, db, detail=True))


class TransferIn(BaseModel):
    """转派入参：新责任人 + 转派原因（选填）+ 乐观锁版本"""
    assignee_id: int
    reason: str | None = None
    expected_version: int | None = None

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, v: str | None):
        return (v or "").strip()[:200] or None


@router.post("/{order_id}/transfer")
def transfer_order(order_id: int, body: TransferIn, db: Session = Depends(get_db),
                   scope: ProjectScope = Depends(require_perm("order:transfer"))):
    """转派（order:transfer：发起巡查员/项目管理员/组织管理员）：待接收/已接收/整改中的工单改派责任人。

    新责任人须为本项目启用的 rectifier 成员；状态保持不变，历史经日志可溯；
    原责任人与新责任人均收到改派通知。
    """
    order = _get_scoped_order(order_id, db, scope)
    new_assignee = db.get(User, body.assignee_id)
    rectifier_membership = (
        db.query(ProjectMember)
        .filter(
            ProjectMember.user_id == body.assignee_id,
            ProjectMember.project_id == scope.project_id,
            ProjectMember.role == "rectifier",
            ProjectMember.active.is_(True),
        )
        .first()
    )
    if new_assignee is None or not new_assignee.active or rectifier_membership is None:
        raise BizError(1011, "转派的责任人不存在或不是本项目整改责任人")
    reassign_order(order, scope.user, db, new_assignee, body.reason or "",
                   expected_version=body.expected_version)
    return ok(order_to_dict(order, db, detail=True))


class CancelIn(BaseModel):
    """作废入参：作废原因（必填）+ 乐观锁版本"""
    reason: str
    expected_version: int | None = None

    @field_validator("reason")
    @classmethod
    def valid_reason(cls, v: str):
        if not v or not v.strip():
            raise ValueError("作废原因必填")
        return v.strip()[:200]


@router.post("/{order_id}/cancel")
def cancel_order_route(order_id: int, body: CancelIn, db: Session = Depends(get_db),
                       scope: ProjectScope = Depends(require_perm("order:cancel"))):
    """作废（order:cancel：发起巡查员/项目管理员/组织管理员）：未闭环未作废的工单终止为 cancelled 终态。

    适用于误建工单等退出场景；责任人与发起人收到作废通知。
    """
    order = _get_scoped_order(order_id, db, scope)
    cancel_order(order, scope.user, db, body.reason, expected_version=body.expected_version)
    return ok(order_to_dict(order, db, detail=True))
