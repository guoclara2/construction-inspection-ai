# 通知通道包（总纲 §7）：统一 ready/send 接口；未配置凭据 → ready=False → status='skipped'，不得抛错阻断业务
from . import inapp, sms, wechat

# channel 名 → 通道模块（dispatch 依据此表路由）
CHANNELS = {"inapp": inapp, "wechat": wechat, "sms": sms}

__all__ = ["CHANNELS", "inapp", "sms", "wechat"]


def get(channel: str):
    """取通道模块；未知通道返回 None（由调用方记 failed）"""
    return CHANNELS.get(channel)
