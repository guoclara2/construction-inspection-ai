# 消息中心路由：站内通知列表 / 已读 / 未读数（notifications 表 inapp 通道，按用户 + 当前项目隔离）
# P06：messages → notifications 重构；列表仅返回 inapp 通道（wechat/sms 行是投递记录，不进消息中心）
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import ProjectScope, _client_ip, project_scope
from ..models import Notification, RectifyOrder
from ..services.audit import write_audit
from ..utils import fmt, ok

router = APIRouter(prefix="/api/messages", tags=["messages"])

# 待办类模板（P2-1）：消息本身在提示"该工单需要您处理"，工单闭环后待办即失效（消息快照保留供审计）
TODO_TEMPLATES = frozenset({
    "ORDER_CREATED",        # 待接收
    "ORDER_FEEDBACK",       # 待审核
    "ORDER_URGE",           # 催办
    "ORDER_WARN",           # 临期提醒
    "ORDER_OVERDUE",        # 已超期
    "ORDER_ESCALATE",       # 超期升级督办
    "ORDER_REVIEW_REJECT",  # 打回继续整改
})


def _scope_filter(scope: ProjectScope):
    """当前用户 + 当前项目（含无项目维度的全局消息），仅站内通道"""
    return (Notification.user_id == scope.user.id,
            Notification.channel == "inapp",
            or_(Notification.project_id == scope.project_id, Notification.project_id.is_(None)))


def message_to_dict(m: Notification, pushed: bool = False, stale: bool = False) -> dict:
    return {
        "id": m.id,
        "title": m.title,
        "content": m.content,
        "order_id": m.biz_id if m.biz_type == "order" else None,
        "project_id": m.project_id,
        "read": m.read,
        "template_code": m.template_code,
        # 通道投递状态（P06 §2.4.3）：同事件 wechat 行 status=sent → 已推送，否则仅站内
        "pushed": pushed,
        # 待办已失效（P2-1）：待办类消息关联工单已闭环，标题展示"（已处理）"且不计入未读角标
        "stale": stale,
        "created_at": fmt(m.created_at),
    }


def _closed_order_map(db: Session, messages: list[Notification]) -> set[int]:
    """批量查询待办类消息关联工单中已闭环的 id 集合（P2-1；列表分页 ≤100 条，一次 in 查询）"""
    order_ids = {
        m.biz_id for m in messages
        if m.template_code in TODO_TEMPLATES and m.biz_type == "order" and m.biz_id
    }
    if not order_ids:
        return set()
    rows = db.query(RectifyOrder.id).filter(RectifyOrder.id.in_(order_ids), RectifyOrder.status == "closed").all()
    return {r[0] for r in rows}


def _pushed_map(db: Session, messages: list[Notification]) -> dict[int, bool]:
    """批量判断站内通知对应事件是否已成功推送微信（dedup_key 同源：事件键:inapp / 事件键:wechat）"""
    keys = [m.dedup_key[: -len(":inapp")] + ":wechat" for m in messages if m.dedup_key]
    if not keys:
        return {}
    rows = db.query(Notification.dedup_key).filter(
        Notification.dedup_key.in_(keys), Notification.channel == "wechat", Notification.status == "sent"
    ).all()
    sent_keys = {r[0] for r in rows}
    result: dict[int, bool] = {}
    for m in messages:
        if not m.dedup_key:
            result[m.id] = False
            continue
        wechat_key = m.dedup_key[: -len(":inapp")] + ":wechat"
        result[m.id] = wechat_key in sent_keys
    return result


@router.get("")
def list_messages(
    unread_only: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    scope: ProjectScope = Depends(project_scope),
):
    """我的消息列表（当前项目 + 全局，仅站内通道；带「已推送微信/仅站内」与「已处理」标识）"""
    q = db.query(Notification).filter(*_scope_filter(scope))
    if unread_only:
        q = q.filter(Notification.read.is_(False))
    total = q.count()
    messages = q.order_by(Notification.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    pushed = _pushed_map(db, messages)
    closed = _closed_order_map(db, messages)
    return ok({"list": [message_to_dict(
                            m, pushed.get(m.id, False),
                            stale=(m.template_code in TODO_TEMPLATES
                                   and m.biz_type == "order" and m.biz_id in closed))
                        for m in messages],
               "total": total, "page": page, "page_size": page_size})


@router.post("/{message_id}/read")
def mark_read(message_id: int, request: Request, db: Session = Depends(get_db),
              scope: ProjectScope = Depends(project_scope)):
    """标记单条已读：非本人消息一律 403 + 审计（不再静默成功）"""
    m = db.get(Notification, message_id)
    if m is None:
        raise HTTPException(status_code=404, detail={"code": 404, "msg": "消息不存在", "data": None})
    if m.user_id != scope.user.id:
        write_audit(db, "message_access_denied", actor_id=scope.user.id, target_type="message",
                    target_id=message_id, result="denied", org_id=scope.org_id, project_id=scope.project_id,
                    ip=_client_ip(request), user_agent=request.headers.get("user-agent"))
        raise HTTPException(status_code=403, detail={"code": 403, "msg": "无权操作该消息", "data": None})
    m.read = True
    db.commit()
    return ok(None)


@router.post("/read-all")
def mark_all_read(db: Session = Depends(get_db), scope: ProjectScope = Depends(project_scope)):
    """全部已读（当前项目 + 全局，仅站内通道）"""
    db.query(Notification).filter(*_scope_filter(scope), Notification.read.is_(False)).update(
        {"read": True}, synchronize_session=False)
    db.commit()
    return ok(None)


@router.get("/unread-count")
def unread_count(db: Session = Depends(get_db), scope: ProjectScope = Depends(project_scope)):
    """未读数（当前项目 + 全局，仅站内通道）。
    P2-1：待办类消息关联工单已闭环的（stale）不计入——避免"消息中心与实际待办脱节"。"""
    from sqlalchemy import and_, select
    closed_ids = select(RectifyOrder.id).where(RectifyOrder.status == "closed").scalar_subquery()
    stale_cond = and_(
        Notification.template_code.in_(TODO_TEMPLATES),
        Notification.biz_type == "order",
        Notification.biz_id.in_(closed_ids),
    )
    count = (
        db.query(Notification)
        .filter(*_scope_filter(scope), Notification.read.is_(False), ~stale_cond)
        .count()
    )
    return ok({"count": count})
