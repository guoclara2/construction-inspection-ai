# 微信订阅消息通道（总纲 §7）：subscribeMessage.send + access_token 缓存（Redis 2 小时）。
# 未配置 WX_APPID / 用户未绑定 / 未授权订阅 → ready=False（记 skipped，不抛错不阻断业务）。
# 模板字段约定：申请订阅消息模板时须含 thing1（标题）/thing2（内容摘要）两个文本字段，此处按该约定组装 data。
import logging

import httpx
from sqlalchemy.orm import Session

from ...config import settings
from ...models import Notification, NotificationTemplate, User, WxSubscribeAuth
from ..kv import get_kv, set_kv

logger = logging.getLogger(__name__)

# access_token 有效期 7200s，缓存 7000s 提前失效（总纲 §7：Redis 缓存，未配置 Redis 降级进程内）
_TOKEN_TTL = 7000
_TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"
_SEND_URL = "https://api.weixin.qq.com/cgi-bin/message/subscribe/send"


def is_configured() -> bool:
    return bool(settings.WX_APPID.strip() and settings.WX_SECRET.strip())


def get_access_token() -> str | None:
    """获取微信接口凭据：优先 KV 缓存，未命中则远端获取并缓存；未配置/失败返回 None"""
    cached = get_kv("wx:access_token")
    if cached:
        return cached
    if not is_configured():
        return None
    try:
        resp = httpx.get(_TOKEN_URL, params={
            "grant_type": "client_credential",
            "appid": settings.WX_APPID,
            "secret": settings.WX_SECRET,
        }, timeout=10)
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("微信 access_token 获取异常: %s", exc)
        return None
    token = data.get("access_token")
    if not token:
        logger.warning("微信 access_token 获取失败: %s", data)
        return None
    set_kv("wx:access_token", token, _TOKEN_TTL)
    return token


def _resolve_template(db: Session, notification: Notification) -> NotificationTemplate | None:
    if not notification.template_code:
        return None
    return (
        db.query(NotificationTemplate)
        .filter(NotificationTemplate.code == notification.template_code)
        .first()
    )


def ready(notification: Notification, db: Session) -> tuple[bool, str | None]:
    """前置检查：配置 / 绑定 / 模板 / 用户授权 任一不满足 → 跳过（skipped）"""
    if not is_configured():
        return False, "微信未配置（WX_APPID/WX_SECRET 为空）"
    user = db.get(User, notification.user_id)
    if user is None or not user.wx_openid:
        return False, "用户未绑定微信"
    template = _resolve_template(db, notification)
    if template is None or not (template.wx_template_id or "").strip():
        return False, "模板未配置微信模板 ID"
    auth = (
        db.query(WxSubscribeAuth)
        .filter(
            WxSubscribeAuth.user_id == notification.user_id,
            WxSubscribeAuth.template_code == notification.template_code,
            WxSubscribeAuth.accepted.is_(True),
        )
        .first()
    )
    if auth is None:
        return False, "用户未授权该订阅消息模板"
    return True, None


def send(notification: Notification, db: Session) -> tuple[bool, str | None]:
    """发送订阅消息：thing1=标题、thing2=内容摘要（模板申请约定，见模块头注释）；
    点击跳转工单详情页（biz_type=order 时）"""
    token = get_access_token()
    if token is None:
        return False, "微信 access_token 不可用"
    template = _resolve_template(db, notification)
    if template is None or not (template.wx_template_id or "").strip():
        return False, "模板未配置微信模板 ID"
    user = db.get(User, notification.user_id)
    if user is None or not user.wx_openid:
        return False, "用户未绑定微信"
    payload = {
        "touser": user.wx_openid,
        "template_id": template.wx_template_id,
        "page": f"pages/order-detail/index?id={notification.biz_id}"
        if notification.biz_type == "order" and notification.biz_id else "pages/index/index",
        "data": {
            "thing1": {"value": notification.title[:20]},
            "thing2": {"value": notification.content[:20]},
        },
    }
    try:
        resp = httpx.post(_SEND_URL, params={"access_token": token}, json=payload, timeout=10)
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        return False, f"微信订阅消息请求异常: {exc}"
    if data.get("errcode") == 0:
        return True, None
    return False, f"微信订阅消息失败: {data.get('errcode')} {data.get('errmsg')}"
