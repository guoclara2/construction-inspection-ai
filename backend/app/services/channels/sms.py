# 短信通道（总纲 §7）：阿里云完整实现 + 腾讯云扩展点。
# 未配置服务商 → ready=False（记 skipped，不抛错不阻断业务）。
# 模板约定：阿里云模板需含 ${content} 变量（通知类，模板 ID 存 notification_templates.sms_template_code）；
#           验证码模板需含 ${code} 变量（模板 ID 为环境变量 SMS_TEMPLATE_CODE）。
import json
import logging
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from ...config import settings
from ...models import Notification, NotificationTemplate, User

logger = logging.getLogger(__name__)

_ALIYUN_ENDPOINT = "https://dysmsapi.aliyuncs.com/"


def is_configured() -> bool:
    return bool(settings.SMS_PROVIDER.strip() and settings.SMS_AK.strip() and settings.SMS_SK.strip()
                and settings.SMS_SIGN.strip())


# ---------- 阿里云短信（RPC 签名 V1，完整实现） ----------

def _penc(value: str) -> str:
    """阿里云 RPC 规范的 percent-encode（保留 -_.~，其余全部编码）"""
    from urllib.parse import quote

    return quote(str(value), safe="-_.~")


def _aliyun_signature(params: dict, secret: str) -> str:
    """构造 RPC V1 签名：排序参数 → 编码查询串 → GET&%2F&<enc> → HMAC-SHA1 → Base64"""
    import base64
    import hashlib
    import hmac

    sorted_qs = "&".join(f"{_penc(k)}={_penc(v)}" for k, v in sorted(params.items()))
    string_to_sign = "GET&%2F&" + _penc(sorted_qs)
    digest = hmac.new((secret + "&").encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def _send_aliyun(phone: str, template_code: str, template_param: dict) -> tuple[bool, str | None]:
    """调用阿里云 SendSms；返回 (ok, error)"""
    params = {
        "AccessKeyId": settings.SMS_AK,
        "Action": "SendSms",
        "Format": "JSON",
        "PhoneNumbers": phone,
        "RegionId": "cn-hangzhou",
        "SignName": settings.SMS_SIGN,
        "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": uuid.uuid4().hex,
        "SignatureVersion": "1.0",
        "TemplateCode": template_code,
        "TemplateParam": json.dumps(template_param, ensure_ascii=False),
        "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "Version": "2017-05-25",
    }
    params["Signature"] = _aliyun_signature(params, settings.SMS_SK)
    try:
        resp = httpx.get(_ALIYUN_ENDPOINT, params=params, timeout=10)
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        return False, f"阿里云短信请求异常: {exc}"
    if data.get("Code") == "OK":
        return True, None
    return False, f"阿里云短信失败: {data.get('Code')} {data.get('Message')}"


def _send_tencent(phone: str, template_code: str, template_param: dict) -> tuple[bool, str | None]:
    """腾讯云短信扩展点（本任务按 P06 §2.1.3 只实现一家完整）：
    接入时在此实现 TC3-HMAC-SHA256 签名调用 sms.tencentcloudapi.com SendSms，
    参数（phone/template_code/template_param）与 _send_aliyun 对齐即可接入。"""
    logger.error("SMS_PROVIDER=tencent 暂未对接，请在 services/channels/sms.py::_send_tencent 补充实现")
    return False, "腾讯云短信服务商未对接"


def _provider_send(phone: str, template_code: str, template_param: dict) -> tuple[bool, str | None]:
    if settings.SMS_PROVIDER == "aliyun":
        return _send_aliyun(phone, template_code, template_param)
    if settings.SMS_PROVIDER == "tencent":
        return _send_tencent(phone, template_code, template_param)
    logger.error("未知 SMS_PROVIDER=%s，支持 aliyun/tencent", settings.SMS_PROVIDER)
    return False, f"未知短信服务商 {settings.SMS_PROVIDER}"


# ---------- 验证码短信（auth 短信验证码路径，模板 ID 走 SMS_TEMPLATE_CODE） ----------

def send_sms(phone: str, code: str) -> tuple[bool, str | None]:
    """发送验证码短信（登录/绑定场景）。未配置服务商或验证码模板 → (False, 原因)，由调用方决定降级"""
    if not is_configured():
        return False, "短信服务未配置"
    if not settings.SMS_TEMPLATE_CODE.strip():
        return False, "验证码短信模板未配置（SMS_TEMPLATE_CODE）"
    return _provider_send(phone, settings.SMS_TEMPLATE_CODE, {"code": code})


# ---------- 通知通道统一接口（ready/send） ----------

def ready(notification: Notification, db: Session) -> tuple[bool, str | None]:
    """前置检查：服务商 / 手机号 / 模板短信模板 ID 任一缺失 → 跳过（skipped）"""
    if not is_configured():
        return False, "短信服务未配置"
    user = db.get(User, notification.user_id)
    if user is None or not (user.phone or "").strip():
        return False, "用户未登记手机号"
    if notification.template_code:
        template = (
            db.query(NotificationTemplate)
            .filter(NotificationTemplate.code == notification.template_code)
            .first()
        )
        if template is None or not (template.sms_template_code or "").strip():
            return False, "模板未配置短信模板 ID"
    return True, None


def send(notification: Notification, db: Session) -> tuple[bool, str | None]:
    """发送通知短信：TemplateParam={"content": 内容摘要}（模板申请约定 ${content}）"""
    user = db.get(User, notification.user_id)
    if user is None or not (user.phone or "").strip():
        return False, "用户未登记手机号"
    template = None
    if notification.template_code:
        template = (
            db.query(NotificationTemplate)
            .filter(NotificationTemplate.code == notification.template_code)
            .first()
        )
    template_code = (template.sms_template_code if template else None) or ""
    if not template_code:
        return False, "模板未配置短信模板 ID"
    return _provider_send(user.phone, template_code, {"content": notification.content[:70]})
