# 多通道通知服务（P06 §2.1 / 总纲 §4.7、§7）：模板渲染 → 每通道落库 pending → 事务提交后投递。
# 只落库不发送：notify() 在业务事务内 add（与状态/日志同事务，总纲 §10），
# 发送由 dispatch_pending()（提交后即时投递）与调度器 retry_notifications（失败重试）执行。
import logging
import re

from sqlalchemy.orm import Session

from ..models import Notification, NotificationTemplate
from ..utils import now_utc, to_local
from . import channels

logger = logging.getLogger(__name__)

# 事件级去重键中并入通道后缀后的唯一键（每通道一行，键不冲突）
def channel_dedup_key(event_key: str, channel: str) -> str:
    return f"{event_key}:{channel}"


# 占位符提取与白名单：仅允许 `{foo}` 简单字段名，杜绝 `{x.__class__}`/`{x[0]}`/`{x!r}` 等
# str.format 字段遍历/格式说明符注入（模板渲染安全基线，P0 注入防御）。
_PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")
_SIMPLE_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# 渲染失败（占位符断言未通过）时的降级文案：不暴露模板内部细节，保证通知仍可达
_FALLBACK_TITLE = "工程巡查通知"
_FALLBACK_CONTENT = "您有一条新的工程巡查通知，请登录小程序查看详情。"


def _assert_placeholders(tpl: str, context: dict) -> list[str]:
    """渲染前对全部占位符做存在性断言：
    ① 仅含简单字段名（含属性/下标/格式说明符的非法 token 一律判为未通过，防注入）；
    ② 字段名必须存在于 context 且值不为 None。
    返回未通过断言的占位符列表（空列表 = 全部断言通过）。"""
    missing: list[str] = []
    for token in _PLACEHOLDER_RE.findall(tpl or ""):
        if not _SIMPLE_FIELD_RE.match(token):
            missing.append(token)
            continue
        if token not in context or context[token] is None:
            missing.append(token)
    return missing


def _render_safe(tpl: str, context: dict) -> str:
    """安全渲染：仅替换白名单字段名占位符，不依赖 str.format（防注入）"""
    def repl(match: re.Match) -> str:
        token = match.group(1)
        if not _SIMPLE_FIELD_RE.match(token) or token not in context:
            return match.group(0)
        return str(context[token])
    return _PLACEHOLDER_RE.sub(repl, tpl or "")


def resolve_channels(db: Session, template: NotificationTemplate, context: dict | None = None) -> list[str]:
    """通道选择策略（P06 §2.1.4）：以模板 channel 字段（逗号分隔）为数据源；
    高风险新工单（ORDER_CREATED 且 risk_level=high）在模板基础上追加 sms 兜底"""
    base = [c.strip() for c in template.channel.split(",") if c.strip()]
    ctx = context or {}
    if template.code == "ORDER_CREATED" and ctx.get("risk_level") == "high" and "sms" not in base:
        base.append("sms")
    return base


def default_dedup_key(template_code: str, user_id: int, biz_id: int | None) -> str:
    """缺省去重键：同一 (template, user, biz, 自然小时) 只发一次（总纲 §7）"""
    hour = to_local(now_utc()).strftime("%Y%m%d%H")
    return f"{template_code}:{user_id}:{biz_id or 0}:{hour}"


def notify(
    db: Session,
    *,
    user_id: int,
    template_code: str,
    context: dict,
    project_id: int | None,
    org_id: int | None = None,
    biz_type: str | None = None,
    biz_id: int | None = None,
    channel_override: list[str] | None = None,
    dedup_key: str | None = None,
) -> list[Notification]:
    """按模板渲染并逐通道落库（status='pending'）。返回本次新建的记录列表。

    - 模板缺失/停用：记错误日志返回 []（不阻断业务）。
    - 去重：dedup_key 为事件键，落库时并入通道后缀取唯一；
      某通道键已存在 → 该通道跳过不写行（同一事件不重复轰炸）。
    - 只落库：真正的发送在事务提交后由 dispatch_pending() 执行。
    """
    template = (
        db.query(NotificationTemplate)
        .filter(NotificationTemplate.code == template_code, NotificationTemplate.enabled.is_(True))
        .first()
    )
    if template is None:
        logger.error("通知模板缺失或已停用：%s（user=%s biz=%s）", template_code, user_id, biz_id)
        return []

    event_key = dedup_key or (f"{template_code}:{user_id}:{biz_id}:v{context['event_version']}"
        if "event_version" in context else default_dedup_key(template_code, user_id, biz_id))

    # 渲染前占位符存在性断言（P0 注入防御 + 完整性）：失败则发送降级文本并告警
    missing = _assert_placeholders(template.title_tpl, context) + _assert_placeholders(template.content_tpl, context)
    if missing:
        logger.error("通知模板渲染失败（占位符缺失/非法）code=%s user=%s biz=%s 缺失=%s",
                     template_code, user_id, biz_id, sorted(set(missing)))
        title, content = _FALLBACK_TITLE, _FALLBACK_CONTENT
    else:
        title = _render_safe(template.title_tpl, context)[:100]
        content = _render_safe(template.content_tpl, context)[:500]
    target_channels = channel_override or resolve_channels(db, template, context)

    created: list[Notification] = []
    for ch in target_channels:
        key = channel_dedup_key(event_key, ch)
        exists = db.query(Notification.id).filter(Notification.dedup_key == key).first()
        if exists is not None:
            continue  # 该通道已发过：去重跳过
        row = Notification(
            org_id=org_id,
            project_id=project_id,
            user_id=user_id,
            title=title,
            content=content,
            biz_type=biz_type,
            biz_id=biz_id,
            channel=ch,
            template_code=template_code,
            status="pending",
            dedup_key=key,
        )
        db.add(row)
        created.append(row)
    if created:
        logger.info("通知落库 %s → user=%s 通道=%s biz=%s",
                    template_code, user_id, [c.channel for c in created], biz_id)
    return created


def _send_one(db: Session, n: Notification) -> None:
    """单条投递：ready 不过 → skipped；send 成功 → sent；异常/失败 → failed。
    任何错误只落库 status/error，绝不抛出（P06 禁止通知失败阻断业务）。"""
    module = channels.get(n.channel)
    if module is None:
        n.status = "failed"
        n.error = f"未知通道 {n.channel}"
        return
    try:
        ok, reason = module.ready(n, db)
        if not ok:
            n.status = "skipped"
            n.error = reason
            return
        ok, error = module.send(n, db)
        if ok:
            n.status = "sent"
            n.sent_at = now_utc()
            n.error = None
        else:
            n.status = "failed"
            n.error = (error or "未知错误")[:500]
    except Exception as exc:  # noqa: BLE001
        n.status = "failed"
        n.error = f"通道异常: {exc}"[:500]


def dispatch_pending(db: Session, limit: int = 200, *, worker: bool = False) -> int:
    """把 pending 通知实际投递（业务事务提交后调用；独立小事务提交，失败不影响主流程）。
    返回本次处理的条数。"""
    from ..config import settings
    if settings.is_prod and not worker:
        return 0
    from datetime import timedelta
    from uuid import uuid4
    from sqlalchemy import update, or_, and_
    now = now_utc()
    eligible = or_(Notification.status == "pending",
        and_(Notification.status == "sending", Notification.lease_until < now),
        and_(Notification.status == "failed", Notification.retry_count < 3,
             Notification.updated_at < now-timedelta(minutes=8)))
    ids = [r[0] for r in db.query(Notification.id).filter(eligible).order_by(Notification.id).limit(limit)]
    count = 0
    for nid in ids:
        token = uuid4().hex
        claimed = db.execute(update(Notification).where(Notification.id==nid, eligible).values(
            status="sending", claim_token=token, lease_until=now_utc()+timedelta(minutes=5),
            retry_count=Notification.retry_count+1)).rowcount
        db.commit()
        if not claimed: continue
        db.expire_all()
        n = db.get(Notification, nid)
        _send_one(db, n)
        final = {"status":n.status,"error":n.error,"sent_at":n.sent_at,"lease_until":None}
        # Remove dirty ORM state: final write must be conditional on this claim token.
        db.expire(n)
        db.execute(update(Notification).where(Notification.id==nid,Notification.claim_token==token).values(**final))
        db.commit(); count += 1
    return count

def notification_tick():
    from ..database import SessionLocal
    with SessionLocal() as db:
        return dispatch_pending(db, worker=True)
