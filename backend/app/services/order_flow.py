# 工单状态机：唯一状态变更入口（不含 create，create 由 confirm 流程处理）
# P05 并发控制（D10）：FOR UPDATE 行锁重加载 + version 乐观锁（expected_version 不匹配 → 1014）
# P06：通知模板化多通道落库（随主事务提交）+ 提交后 dispatch_pending 即时投递（总纲 §10）
import logging

from sqlalchemy import update
from sqlalchemy.orm import Session

from ..p1_models import FeedbackRound
from ..models import OrderLog, OrderNoSeq, Project, RectifyOrder, User
from ..utils import BizError, now_utc, to_local
from .notify import dispatch_pending, notify

logger = logging.getLogger(__name__)

# 合法流转表：action → (from_status, to_status)
TRANSITIONS = {
    "accept": ("pending", "accepted"),
    "start": ("accepted", "processing"),
    "feedback": ("processing", "review"),
    "review_pass": ("review", "closed"),
    "review_reject": ("review", "processing"),
}


def next_order_no(db: Session, project: Project) -> str:
    """Allocate in the caller's transaction; the row lock lasts until commit.

    MySQL has no INSERT RETURNING. Initialize idempotently, increment under its
    write lock, then use a locking read (also current under REPEATABLE READ).
    """
    from sqlalchemy import select
    from .insert_once import insert_once
    date_str = to_local(now_utc()).strftime("%Y%m%d")
    dialect = db.bind.dialect.name
    db.execute(insert_once(OrderNoSeq.__table__,
        dict(project_id=project.id, date=date_str, seq=0), ['project_id', 'date'], dialect))
    condition = (OrderNoSeq.project_id == project.id, OrderNoSeq.date == date_str)
    db.execute(update(OrderNoSeq).where(*condition).values(seq=OrderNoSeq.seq + 1))
    seq = db.execute(select(OrderNoSeq.seq).where(*condition).with_for_update()).scalar_one()
    code = f"P{project.id}"
    return f"WO-{code}-{date_str}-{seq:04d}"


def _lock_order(db: Session, order: RectifyOrder) -> RectifyOrder:
    """丢弃会话缓存状态后以 SELECT ... FOR UPDATE 重加载工单。

    PostgreSQL 生成行锁，状态判断与写入在同一把锁内；SQLite 方言忽略锁子句
    （依赖其单写者模型），expire 仍保证读到最新已提交状态。
    """
    db.expire(order)
    locked = db.get(RectifyOrder, order.id, with_for_update=True)
    if locked is None:
        raise BizError(1020, "工单不存在")
    return locked


def transition(order: RectifyOrder, action: str, operator: User, db: Session, payload: dict | None = None,
               expected_version: int | None = None) -> RectifyOrder:
    """执行一次合法状态流转：行锁重加载 → 版本校验 → 守卫式原子 UPDATE(version+1) → 写日志 →（提交后）通知

    并发正确性双保险（D10）：PostgreSQL 靠 FOR UPDATE 行锁串行化；SQLite 方言忽略行锁，
    由「状态+版本为条件的守卫 UPDATE」在写入瞬间原子复核——rowcount=0 即并发失效，
    回滚后按最新状态区分 400（状态不允许）与 1014（版本冲突）。
    """
    if action not in TRANSITIONS:
        raise BizError(400, "未知操作")
    from_status, to_status = TRANSITIONS[action]

    locked = _lock_order(db, order)
    # 乐观锁：客户端携带的版本与当前不一致 → 已被他人更新（P05 §2.6）
    if expected_version is not None and locked.version != expected_version:
        raise BizError(1014, "工单已被他人更新，请刷新后重试")
    if locked.status != from_status:
        raise BizError(400, "当前状态不允许该操作")
    payload = payload or {}
    remark = None
    values: dict = {"status": to_status, "version": (locked.version or 0) + 1}

    round_number = locked.round
    if action.startswith("review_") and operator.id == locked.assignee_id:
        raise BizError(403, "整改责任人不可审核本人整改")
    if action == "feedback":
        db.add(FeedbackRound(order_id=locked.id, round=round_number, text=payload["text"],
                             submitter_id=operator.id, submitted_at=now_utc()))
        from ..routers.attachments import bind_attachment
        for extra in payload.get("attachments", []):
            bind_attachment(extra, locked.id, round_number)
        # 反馈照片附件化（P04 §2.5.2）：附件由路由层 load_bindable_attachment 校验后传入，
        # 此处绑定到本工单本轮反馈（绑定后 biz_id 不可改）
        att = payload["attachment"]
        bind_attachment(att, locked.id, locked.round)
        values["feedback_text"] = payload["text"]
        remark = (payload.get("text") or "")[:100]
    elif action == "review_pass":
        values["review_result"] = "pass"
        values["review_comment"] = payload.get("comment")
        values["closed_at"] = now_utc()
        remark = payload.get("comment")
    elif action == "review_reject":
        # 打回：round+1；反馈附件保留归档（随原 round 留存，消灭 G4 一半，P04 §2.5.4），
        # 当前轮次尚未提交反馈 → feedback_text 清空；历史轮次完整可溯于 attachments + 日志
        values["round"] = locked.round + 1
        values["feedback_text"] = None
        values["review_result"] = "reject"
        values["review_comment"] = payload.get("comment")
        remark = payload.get("comment")

    if action.startswith("review_"):
        archived = db.query(FeedbackRound).filter_by(order_id=locked.id, round=round_number).first()
        if archived is None:
            archived = FeedbackRound(order_id=locked.id, round=round_number,
                text=locked.feedback_text or "[旧数据未保留完整反馈正文]", submitter_id=locked.assignee_id)
            db.add(archived)
        archived.reviewer_id, archived.reviewed_at = operator.id, now_utc()
        archived.result, archived.comment = action.removeprefix("review_"), payload.get("comment")

    # 守卫式原子更新：状态与版本在写入瞬间未变才生效（SQLite 无行锁下的最终裁决）
    res = db.execute(
        update(RectifyOrder)
        .where(RectifyOrder.id == locked.id,
               RectifyOrder.status == from_status,
               RectifyOrder.version == locked.version)
        .values(**values)
    )
    if res.rowcount != 1:
        db.rollback()
        fresh = db.get(RectifyOrder, order.id)
        if fresh is not None and fresh.status != from_status:
            raise BizError(400, "当前状态不允许该操作")
        raise BizError(1014, "工单已被他人更新，请刷新后重试")
    db.expire(locked)  # 丢弃内存旧值，后续读取走库内新值

    db.add(OrderLog(
        order_id=locked.id,
        from_status=from_status,
        to_status=to_status,
        operator_id=operator.id,
        action=action,
        remark=remark,
    ))

    # 通知相关方（P06 模板化多通道）：随主事务落库，提交后由 dispatch_pending 投递（总纲 §10 事务契约）
    pid, oid = locked.project_id, locked.org_id
    ctx = {"order_no": locked.order_no, "event_version": locked.version}
    if action == "accept":
        notify(db, user_id=locked.inspector_id, template_code="ORDER_ACCEPTED", context=ctx,
               project_id=pid, org_id=oid, biz_type="order", biz_id=locked.id)
    elif action == "start":
        notify(db, user_id=locked.inspector_id, template_code="ORDER_STARTED", context=ctx,
               project_id=pid, org_id=oid, biz_type="order", biz_id=locked.id)
    elif action == "feedback":
        notify(db, user_id=locked.inspector_id, template_code="ORDER_FEEDBACK", context=ctx,
               project_id=pid, org_id=oid, biz_type="order", biz_id=locked.id)
    elif action == "review_pass":
        notify(db, user_id=locked.assignee_id, template_code="ORDER_REVIEW_PASS", context=ctx,
               project_id=pid, org_id=oid, biz_type="order", biz_id=locked.id)
    elif action == "review_reject":
        notify(db, user_id=locked.assignee_id, template_code="ORDER_REVIEW_REJECT",
               context=dict(ctx, round=locked.round), project_id=pid, org_id=oid,
               biz_type="order", biz_id=locked.id)

    db.commit()
    db.refresh(locked)
    # 通知投递在事务提交后执行：通道失败只落 status/error，不影响主流程（P06 §三）
    dispatch_pending(db)
    logger.info("工单 %s %s: %s → %s（操作人 %s，version=%s）",
                locked.order_no, action, from_status, to_status, operator.id, locked.version)
    return locked


def log_urge(order: RectifyOrder, operator: User, db: Session, expected_version: int | None = None):
    """admin 催办：写 urge 日志（from/to 相同，不变更状态）并通知责任人（P06 模板化多通道，提交后投递）"""
    locked = _lock_order(db, order)
    if expected_version is not None and locked.version != expected_version:
        raise BizError(1014, "工单已被他人更新，请刷新后重试")
    db.add(OrderLog(
        order_id=locked.id,
        from_status=locked.status,
        to_status=locked.status,
        operator_id=operator.id,
        action="urge",
        remark="管理员催办",
    ))
    notify(db, user_id=locked.assignee_id, template_code="ORDER_URGE",
           context={"order_no": locked.order_no},
           project_id=locked.project_id, org_id=locked.org_id,
           biz_type="order", biz_id=locked.id)
    db.commit()
    dispatch_pending(db)
    logger.info("工单 %s 催办（操作人 %s）", locked.order_no, operator.id)


def reassign_order(order: RectifyOrder, operator: User, db: Session, new_assignee: User,
                   reason: str, expected_version: int | None = None) -> RectifyOrder:
    """转派（order:transfer）：待接收/已接收/整改中的工单改派给其他责任人。

    - 状态保持不变（新责任人从当前进度继续），完整流转历史经 OrderLog 可溯；
    - 守卫式原子 UPDATE（status+version+assignee 条件，version+1），与 transition 同套并发保证；
    - 通知原责任人与新责任人（模板 ORDER_TRANSFER，dedup_key 带版本号使多次转派均可送达）。
    """
    locked = _lock_order(db, order)
    if expected_version is not None and locked.version != expected_version:
        raise BizError(1014, "工单已被他人更新，请刷新后重试")
    if locked.status not in ("pending", "accepted", "processing"):
        raise BizError(400, "待审核/已闭环/已作废的工单不可转派")
    old_assignee_id = locked.assignee_id
    if old_assignee_id == new_assignee.id:
        raise BizError(400, "新责任人与当前责任人相同，无需转派")
    res = db.execute(
        update(RectifyOrder)
        .where(RectifyOrder.id == locked.id,
               RectifyOrder.status == locked.status,
               RectifyOrder.version == locked.version,
               RectifyOrder.assignee_id == old_assignee_id)
        .values(assignee_id=new_assignee.id, version=(locked.version or 0) + 1)
    )
    if res.rowcount != 1:
        db.rollback()
        raise BizError(1014, "工单已被他人更新，请刷新后重试")
    db.expire(locked)

    reason = (reason or "").strip() or "未填写"
    db.add(OrderLog(
        order_id=locked.id,
        from_status=locked.status,
        to_status=locked.status,
        operator_id=operator.id,
        action="transfer",
        remark=f"改派给 {new_assignee.name}，原因：{reason}",
    ))
    ctx = {"order_no": locked.order_no, "new_assignee_name": new_assignee.name, "reason": reason}
    version = (locked.version or 0)
    if old_assignee_id != operator.id:
        notify(db, user_id=old_assignee_id, template_code="ORDER_TRANSFER", context=ctx,
               project_id=locked.project_id, org_id=locked.org_id,
               biz_type="order", biz_id=locked.id, dedup_key=f"transfer:old:{locked.id}:v{version}")
    notify(db, user_id=new_assignee.id, template_code="ORDER_TRANSFER", context=ctx,
           project_id=locked.project_id, org_id=locked.org_id,
           biz_type="order", biz_id=locked.id, dedup_key=f"transfer:new:{locked.id}:v{version}")
    db.commit()
    db.refresh(locked)
    dispatch_pending(db)
    logger.info("工单 %s 转派给 %s（操作人 %s）", locked.order_no, new_assignee.id, operator.id)
    return locked


def cancel_order(order: RectifyOrder, operator: User, db: Session, reason: str,
                 expected_version: int | None = None) -> RectifyOrder:
    """作废（order:cancel）：未闭环未作废的工单终止为 cancelled（终态，误建工单退出路径）。

    - 守卫式原子 UPDATE（status+version 条件，version+1）；
    - 通知责任人与发起人（模板 ORDER_CANCELLED；操作人自身不重复通知）。
    """
    locked = _lock_order(db, order)
    if expected_version is not None and locked.version != expected_version:
        raise BizError(1014, "工单已被他人更新，请刷新后重试")
    if locked.status in ("closed", "cancelled"):
        raise BizError(400, "工单已闭环或已作废，无需重复操作")
    from_status = locked.status
    res = db.execute(
        update(RectifyOrder)
        .where(RectifyOrder.id == locked.id,
               RectifyOrder.status == from_status,
               RectifyOrder.version == locked.version)
        .values(status="cancelled", version=(locked.version or 0) + 1)
    )
    if res.rowcount != 1:
        db.rollback()
        fresh = db.get(RectifyOrder, order.id)
        if fresh is not None and fresh.status in ("closed", "cancelled"):
            raise BizError(400, "工单已闭环或已作废，无需重复操作")
        raise BizError(1014, "工单已被他人更新，请刷新后重试")
    db.expire(locked)

    db.add(OrderLog(
        order_id=locked.id,
        from_status=from_status,
        to_status="cancelled",
        operator_id=operator.id,
        action="cancel",
        remark=reason,
    ))
    ctx = {"order_no": locked.order_no, "reason": reason}
    for uid in {locked.assignee_id, locked.inspector_id} - {operator.id}:
        notify(db, user_id=uid, template_code="ORDER_CANCELLED", context=ctx,
               project_id=locked.project_id, org_id=locked.org_id,
               biz_type="order", biz_id=locked.id)
    db.commit()
    db.refresh(locked)
    dispatch_pending(db)
    logger.info("工单 %s 作废（操作人 %s，原因：%s）", locked.order_no, operator.id, reason)
    return locked
