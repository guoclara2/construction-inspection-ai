# 用户自助路由：微信订阅消息授权查询与上报（P06 §2.4：后端只对已授权用户发订阅消息）
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import get_current_user
from ..database import get_db
from ..models import NotificationTemplate, User, WxSubscribeAuth
from ..utils import now_utc, ok

router = APIRouter(prefix="/api/users", tags=["users"])


class WxSubscribeIn(BaseModel):
    """wx.requestSubscribeMessage 回调结果上报：auths = {模板code: 'accept'|'reject'|'ban'}"""
    auths: dict[str, str]


@router.get("/me/wx-subscribe")
def list_wx_subscribe_templates(db: Session = Depends(get_db),
                                user: User = Depends(get_current_user)):
    """可订阅的微信模板清单（已启用且配置了 wx_template_id）：
    小程序据此调 wx.requestSubscribeMessage（按 wx_template_id 去重请求），再上报授权结果。"""
    rows = (
        db.query(NotificationTemplate)
        .filter(
            NotificationTemplate.enabled.is_(True),
            NotificationTemplate.wx_template_id.isnot(None),
            NotificationTemplate.wx_template_id != "",
        )
        .all()  # 结果集有界：模板表量级 < 20
    )
    return ok({"templates": [
        {"code": r.code, "wx_template_id": r.wx_template_id} for r in rows
    ]})


@router.post("/me/wx-subscribe")
def report_wx_subscribe(body: WxSubscribeIn, db: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    """上报订阅消息授权结果：upsert wx_subscribe_auth（user+template 唯一）。
    accept → accepted=true；reject/ban → accepted=false（后续可再次请求授权覆盖）。"""
    now = now_utc()
    updated, skipped = 0, 0
    for template_code, result in body.auths.items():
        if result not in ("accept", "reject", "ban"):
            skipped += 1
            continue
        row = (
            db.query(WxSubscribeAuth)
            .filter(WxSubscribeAuth.user_id == user.id, WxSubscribeAuth.template_code == template_code)
            .first()
        )
        accepted = result == "accept"
        if row is None:
            db.add(WxSubscribeAuth(
                user_id=user.id, template_code=template_code[:50],
                accepted=accepted, subscribed_at=now if accepted else None,
            ))
        else:
            row.accepted = accepted
            row.subscribed_at = now if accepted else None
        updated += 1
    db.commit()
    return ok({"updated": updated, "skipped": skipped})
