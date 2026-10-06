# 开发态登录：仅当 APP_ENV=dev 且 DEV_LOGIN_ENABLED=true 时由 main.py 条件注册。
# P0-2 三重防护：① APP_ENV=prod 不注册路由；② DEV_LOGIN_ENABLED 显式开启；
# ③ 请求来源 IP 必须在 DEV_LOGIN_ALLOWED_IPS 白名单内（默认仅本机回环）。
# 任一不满足均返回 404（不暴露路由存在性）。
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import build_login_data, user_projects
from ..config import settings
from ..database import get_db
from ..models import User
from ..utils import BizError, ok

# dev-accounts 只暴露 3 个演示账号（不再返回全部用户，避免成为攻击清单）
_DEMO_USERNAMES = ["admin", "liming", "zhaoqiang"]


def _ip_matches(client_ip: str, pattern: str) -> bool:
    """白名单条目匹配：精确 IP 或尾缀通配（如 192.168.1.*，便于真机调试放行局域网段）"""
    if pattern.endswith("*"):
        return client_ip.startswith(pattern[:-1])
    return client_ip == pattern


def _require_allowed_ip(request: Request) -> None:
    """来源 IP 门禁：不在 DEV_LOGIN_ALLOWED_IPS 白名单内一律 404。
    只取直连对端地址，不信任可伪造的 X-Forwarded-For。
    条目支持尾缀通配（192.168.1.*）：真机调试时手机经局域网访问，IP 不固定，
    可按网段放行（仅 dev + DEV_LOGIN_ENABLED 双开关生效，风险可控）。"""
    client_ip = request.client.host if request.client else None
    allowed = settings.dev_login_allowed_ips
    if client_ip is None or not any(_ip_matches(client_ip, p) for p in allowed):
        raise HTTPException(status_code=404, detail={"code": 404, "msg": "接口不存在", "data": None})


router = APIRouter(prefix="/api/auth", tags=["auth-dev"], dependencies=[Depends(_require_allowed_ip)])


class DevLoginIn(BaseModel):
    user_id: int


@router.post("/dev-login")
def dev_login(body: DevLoginIn, request: Request, db: Session = Depends(get_db)):
    """开发登录：按 user_id 直接签发令牌（仅 dev + DEV_LOGIN_ENABLED + 白名单 IP）"""
    user = db.get(User, body.user_id)
    if user is None or not user.active or user.status == "disabled":
        raise BizError(1001, "账号不存在或已停用")
    data = build_login_data(db, user, request)
    db.commit()
    return ok(data)


@router.get("/dev-accounts")
def dev_accounts(db: Session = Depends(get_db)):
    """开发模式演示账号列表：仅 3 个固定演示账号（角色取自默认项目成员关系）"""
    users = db.query(User).filter(User.username.in_(_DEMO_USERNAMES)).order_by(User.id).all()
    result = []
    for u in users:
        projects, default_pid = user_projects(db, u)
        roles = next((p["roles"] for p in projects if p["id"] == default_pid), [])
        result.append({"user_id": u.id, "name": u.name, "roles": roles, "is_org_admin": u.is_org_admin})
    return ok(result)
