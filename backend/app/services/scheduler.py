# 定时任务注册与超期治理扫描（P06 §2.2 / 总纲 §7）：
# APScheduler BackgroundScheduler，在 app.main lifespan 中启停；
# 多实例保护：Redis SETNX+TTL 分布式锁，未配置 Redis 时打印告警（假设单实例部署）。
import logging
from contextlib import contextmanager
from datetime import timedelta

from ..config import settings
from ..database import SessionLocal
from ..models import Notification, OrderLog, ProjectMember, RectifyOrder, User
from ..utils import as_utc, now_utc, to_local
from . import kv
from .notify import dispatch_pending, notify
from .sla import resolve_escalation

logger = logging.getLogger(__name__)

# 任务锁 TTL：须大于单次扫描最长耗时（15 分钟级任务的保守上限 10 分钟）
_LOCK_TTL = 600


@contextmanager
def _task_lock(name: str, ttl: int = _LOCK_TTL):
    """Redis SETNX 分布式锁：同一任务同一时刻只有一个实例执行。
    未配置 Redis → 告警并放行（单实例部署假设，总纲 §2 技术栈约定）。"""
    client = kv._get_redis()
    if client is None:
        logger.warning("任务 %s：未配置 Redis，无分布式锁保护（仅单实例部署安全）", name)
        yield True
        return
    key = f"sched:lock:{name}"
    got = False
    try:
        token = __import__("uuid").uuid4().hex
        got = bool(client.set(key, token, nx=True, ex=ttl))
        if not got:
            logger.info("任务 %s：其他实例持有锁，本次跳过", name)
        yield got
    finally:
        if got:
            try:
                client.eval("if redis.call('get',KEYS[1]) == ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end", 1, key, token)
            except Exception as exc:  # noqa: BLE001
                logger.warning("任务 %s：释放锁失败（将随 TTL 过期）: %s", name, exc)


def _fmt_hours(delta: timedelta) -> str:
    """超期/剩余时长文本（小时，保留 1 位）"""
    return f"{delta.total_seconds() / 3600:.1f}"


# ---------- 六大定时任务（总纲 §7 表） ----------

def scan_warn_orders() -> int:
    """每 15 分钟：now >= warn_at 且未闭环/未作废 且未发过 ORDER_WARN → 临期提醒责任人。
    去重：dedup_key=ORDER_WARN:{order_id}（一单只提醒一次，终身一次）。
    已超期的单不再发临期提醒（由 scan_overdue_orders 负责）。返回本次通知数。"""
    with _task_lock("scan_warn_orders") as got:
        if not got:
            return 0
        db = SessionLocal()
        try:
            now = now_utc()
            orders = (
                db.query(RectifyOrder)
                .filter(
                    RectifyOrder.status.notin_(("closed", "cancelled")),
                    RectifyOrder.warn_at.isnot(None),
                    RectifyOrder.warn_at <= now,
                    RectifyOrder.deadline > now,  # 已超期的走 ORDER_OVERDUE，不重复提醒
                )
                .all()
            )
            count = 0
            for order in orders:
                remain = _fmt_hours(as_utc(order.deadline) - now)
                created = notify(
                    db,
                    user_id=order.assignee_id,
                    template_code="ORDER_WARN",
                    context={"order_no": order.order_no, "remain_hours": remain},
                    project_id=order.project_id,
                    org_id=order.org_id,
                    biz_type="order",
                    biz_id=order.id,
                    dedup_key=f"ORDER_WARN:{order.id}",
                )
                count += len(created)
            db.commit()
            dispatch_pending(db)
            if count:
                logger.info("临期提醒扫描：%d 张工单触发 ORDER_WARN", count)
            return count
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.exception("scan_warn_orders 执行失败: %s", exc)
            return 0
        finally:
            db.close()


def scan_overdue_orders() -> int:
    """每 15 分钟：now > deadline 且未闭环/未作废 → ORDER_OVERDUE（每天最多一次）。
    首次超期额外写 order_logs(action='escalate', operator_id=NULL 系统动作)。返回本次通知数。"""
    with _task_lock("scan_overdue_orders") as got:
        if not got:
            return 0
        db = SessionLocal()
        try:
            now = now_utc()
            day = to_local(now).strftime("%Y%m%d")
            orders = (
                db.query(RectifyOrder)
                .filter(
                    RectifyOrder.status.notin_(("closed", "cancelled")),
                    RectifyOrder.deadline < now,
                )
                .all()
            )
            count = 0
            for order in orders:
                overdue_hours = _fmt_hours(now - as_utc(order.deadline))
                # 首次超期：写系统 escalate 日志（operator_id 为空=系统调度动作）
                has_escalate_log = (
                    db.query(OrderLog.id)
                    .filter(OrderLog.order_id == order.id, OrderLog.action == "escalate")
                    .first()
                )
                if has_escalate_log is None:
                    db.add(OrderLog(
                        order_id=order.id,
                        from_status=order.status,
                        to_status=order.status,
                        operator_id=None,
                        action="escalate",
                        remark="系统标记超期",
                    ))
                created = notify(
                    db,
                    user_id=order.assignee_id,
                    template_code="ORDER_OVERDUE",
                    context={"order_no": order.order_no, "overdue_hours": overdue_hours},
                    project_id=order.project_id,
                    org_id=order.org_id,
                    biz_type="order",
                    biz_id=order.id,
                    dedup_key=f"ORDER_OVERDUE:{order.id}:{day}",  # 每天最多一次
                )
                count += len(created)
            db.commit()
            dispatch_pending(db)
            if count:
                logger.info("超期扫描：%d 张工单触发 ORDER_OVERDUE（自然日去重）", count)
            return count
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.exception("scan_overdue_orders 执行失败: %s", exc)
            return 0
        finally:
            db.close()


def escalate_overdue() -> int:
    """每小时：超期时长 > escalate_after_hours → 按 escalate_to_role 通知项目管理员；
    再超一档（> 2×escalate_after_hours）追加通知组织管理员。一单一档一次（dedup 分档）。"""
    with _task_lock("escalate_overdue") as got:
        if not got:
            return 0
        db = SessionLocal()
        try:
            now = now_utc()
            orders = (
                db.query(RectifyOrder)
                .filter(
                    RectifyOrder.status.notin_(("closed", "cancelled")),
                    RectifyOrder.deadline < now,
                )
                .all()
            )
            count = 0
            for order in orders:
                overdue_h = (now - as_utc(order.deadline)).total_seconds() / 3600
                escalate_after, escalate_role = resolve_escalation(db, order)
                if overdue_h <= escalate_after:
                    continue
                overdue_hours = _fmt_hours(now - as_utc(order.deadline))
                context = {"order_no": order.order_no, "overdue_hours": overdue_hours,
                           "assignee_name": None}
                assignee = db.get(User, order.assignee_id)
                context["assignee_name"] = assignee.name if assignee else "责任人"

                # 一档：按规则指定的角色（默认项目管理员）
                level1_role = escalate_role if escalate_role in ("project_admin", "org_admin") else "project_admin"
                targets: list[tuple[int, str]] = []
                if level1_role == "project_admin":
                    rows = (
                        db.query(User.id)
                        .join(ProjectMember, ProjectMember.user_id == User.id)
                        .filter(
                            ProjectMember.project_id == order.project_id,
                            ProjectMember.role == "project_admin",
                            ProjectMember.active.is_(True),
                            User.active.is_(True),
                        )
                        .distinct()
                        .all()
                    )
                    targets = [(r[0], "项目管理员") for r in rows]
                else:  # 规则直接指定组织管理员
                    rows = (
                        db.query(User.id)
                        .filter(User.is_org_admin.is_(True), User.org_id == order.org_id, User.active.is_(True))
                        .all()
                    )
                    targets = [(r[0], "组织管理员") for r in rows]

                # 二档：超期再翻一档 → 追加组织管理员
                level2 = overdue_h > escalate_after * 2
                if level2:
                    rows = (
                        db.query(User.id)
                        .filter(User.is_org_admin.is_(True), User.org_id == order.org_id, User.active.is_(True))
                        .all()
                    )
                    seen = {uid for uid, _ in targets}
                    targets += [(r[0], "组织管理员") for r in rows if r[0] not in seen]

                for uid, role_name in targets:
                    level = 2 if (level2 and role_name == "组织管理员" and level1_role == "project_admin") else 1
                    ctx = dict(context, role_name=role_name)
                    created = notify(
                        db,
                        user_id=uid,
                        template_code="ORDER_ESCALATE",
                        context=ctx,
                        project_id=order.project_id,
                        org_id=order.org_id,
                        biz_type="order",
                        biz_id=order.id,
                        dedup_key=f"ORDER_ESCALATE:{order.id}:{level}:{uid}",  # 一单一档一接收人一次
                    )
                    count += len(created)
            db.commit()
            dispatch_pending(db)
            if count:
                logger.info("超期升级扫描：%d 条 ORDER_ESCALATE 通知", count)
            return count
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            logger.exception("escalate_overdue 执行失败: %s", exc)
            return 0
        finally:
            db.close()


def generate_plan_tasks():
    from .operations import plan_tick
    with _task_lock("plan_tasks") as got:
        return plan_tick() if got else 0

def close_missed_plan_tasks():
    return generate_plan_tasks()

def retry_notifications():
    from .notify import notification_tick
    return notification_tick()


# ---------- 调度器构建 ----------

def build_scheduler():
    """构建并注册六大任务（不启动；由 app.main lifespan start/shutdown）。
    频率契约见总纲 §7 表；计划任务两个调度位为 P08 预留。"""
    from apscheduler.schedulers.background import BackgroundScheduler

    scheduler = BackgroundScheduler(timezone=settings.APP_TIMEZONE, daemon=True)
    scheduler.add_job(scan_warn_orders, "interval", minutes=15, id="scan_warn_orders",
                      max_instances=1, coalesce=True)
    scheduler.add_job(scan_overdue_orders, "interval", minutes=15, id="scan_overdue_orders",
                      max_instances=1, coalesce=True)
    scheduler.add_job(escalate_overdue, "interval", hours=1, id="escalate_overdue",
                      max_instances=1, coalesce=True)
    from .operations import process_ai_jobs
    from .notify import notification_tick
    if settings.AI_WORKER_ENABLED:
        logger.info("AI_WORKER_ENABLED=true：AI 推理队列由独立 worker 进程消费，API 进程跳过 ai_jobs 调度")
    else:
        scheduler.add_job(process_ai_jobs, "interval", seconds=3, id="ai_jobs", max_instances=2, coalesce=True)
    scheduler.add_job(notification_tick, "interval", seconds=5, id="notification_outbox", max_instances=1, coalesce=True)
    scheduler.add_job(generate_plan_tasks, "interval", minutes=1, id="plan_recovery", max_instances=1, coalesce=True)
    return scheduler
