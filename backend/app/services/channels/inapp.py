# 站内通道：落库即送达（消息中心拉取展示），无外部依赖、恒可用
from sqlalchemy.orm import Session

from ...models import Notification


def ready(notification: Notification, db: Session) -> tuple[bool, str | None]:
    """站内通道永可用"""
    return True, None


def send(notification: Notification, db: Session) -> tuple[bool, str | None]:
    """站内消息在落库瞬间即视为送达（unread 红点由消息中心拉取）"""
    return True, None
