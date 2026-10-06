# 认证模块：access/refresh 令牌、令牌吊销、密码策略、登录限流锁定、微信登录绑定、审计
import hashlib
import logging
import secrets
from datetime import timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .models import Project, ProjectMember, RefreshToken, User
from .security import generate_strong_password, hash_password, validate_password_strength, verify_password  # noqa: F401
from .services.audit import write_audit
from .services.channels import sms as sms_channel
from .services.kv import del_kv, set_kv, consume_kv
from .services.ratelimit import get_remaining, hit
from .utils import BizError, now_utc, ok, user_to_dict

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

bearer_scheme = HTTPBearer(auto_error=False)

# must_change_password 用户仍可访问的接口（改密自救路径）
_MUST_CHANGE_ALLOW = {"/api/auth/me", "/api/auth/password", "/api/auth/logout"}
_LOCK_THRESHOLD = 5
_LOCK_MINUTES = 15


# ---------- 工具 ----------

def _client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def _sha256(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_access_token(user: User, project_id: int | None = None) -> str:
    """签发 access token：claims sub/org/pid/tv/jti，HS256。pid=当前项目（P03 切换后动态更新）"""
    now = now_utc()
    payload = {
        "sub": str(user.id),
        "org": user.org_id,
        "pid": project_id,
        "tv": user.token_version,
        "jti": secrets.token_hex(8),
        "iat": int(now.timestamp()),
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


def user_projects(db: Session, user: User) -> tuple[list[dict], int | None]:
    """汇总用户可进入的项目及默认项目 id。org_admin 额外可见本组织全部项目。"""
    members = (
        db.query(ProjectMember)
        .filter(ProjectMember.user_id == user.id, ProjectMember.active.is_(True))
        .order_by(ProjectMember.project_id)
        .all()  # 结果集有界：单用户成员关系行（项目切换下拉，量级 < 数十）
    )
    proj_map: dict[int, list[ProjectMember]] = {}
    for m in members:
        proj_map.setdefault(m.project_id, []).append(m)

    projects: list[dict] = []
    default_pid: int | None = None
    for pid in sorted(proj_map):
        proj = db.get(Project, pid)
        if proj is None:
            continue
        ms = proj_map[pid]
        is_default = any(m.is_default for m in ms)
        projects.append({
            "id": proj.id, "name": proj.name, "code": proj.code,
            "roles": [m.role for m in ms], "org_unit": ms[0].org_unit, "is_default": is_default,
        })
        if is_default:
            default_pid = pid

    # 组织管理员：本组织内未显式加入的项目也可进入（角色视为 project_admin 级）
    if user.is_org_admin and user.org_id is not None:
        # 结果集有界：单组织项目数（项目切换下拉）
        for proj in db.query(Project).filter(Project.org_id == user.org_id).order_by(Project.id).all():
            if proj.id not in proj_map:
                projects.append({
                    "id": proj.id, "name": proj.name, "code": proj.code,
                    "roles": ["project_admin"], "org_unit": None, "is_default": False,
                })

    if default_pid is None and projects:
        default_pid = projects[0]["id"]
    return projects, default_pid


def issue_refresh_token(db: Session, user: User, request: Request | None) -> str:
    """签发并落库一个一次性 refresh token，返回明文（仅此一次可见）"""
    raw = secrets.token_urlsafe(48)
    row = RefreshToken(
        user_id=user.id,
        token_hash=_sha256(raw),
        expires_at=now_utc() + timedelta(days=settings.REFRESH_TOKEN_DAYS),
        revoked=False,
        ua=((request.headers.get("user-agent") or "")[:300] or None) if request else None,
        ip=_client_ip(request),
    )
    db.add(row)
    db.flush()
    return raw


def revoke_all_refresh(db: Session, user_id: int) -> None:
    db.query(RefreshToken).filter(RefreshToken.user_id == user_id, RefreshToken.revoked.is_(False)).update(
        {"revoked": True}, synchronize_session=False
    )


def _unauthorized(msg: str = "登录已失效"):
    return HTTPException(status_code=401, detail={"code": 401, "msg": msg, "data": None})


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise _unauthorized()
    try:
        payload = jwt.decode(credentials.credentials, settings.JWT_SECRET, algorithms=["HS256"])
        user_id = int(payload.get("sub"))
        tv = payload.get("tv")
    except (JWTError, TypeError, ValueError):
        raise _unauthorized()
    # 暂存 JWT 中的当前项目 id，供 deps.get_current_project_id 在缺少 X-Project-Id 头时回退
    request.state.jwt_pid = payload.get("pid")
    user = db.get(User, user_id)
    if user is None or not user.active or user.status == "disabled":
        raise _unauthorized()
    # 令牌版本比对：改密/停用/角色变更/重置密码后旧令牌立即失效
    if tv is None or int(tv) != user.token_version:
        raise _unauthorized()
    # 强制改密拦截：除自救路径外一律 1060
    if user.must_change_password and request.url.path not in _MUST_CHANGE_ALLOW:
        raise BizError(1060, "请先修改初始密码")
    return user


# ---------- 入参模型 ----------

class WxLoginIn(BaseModel):
    # 所有部署均由服务端校验 wx.login code，不把客户端请求头作为身份证明。
    code: str = ""


class WxBindIn(BaseModel):
    bind_ticket: str
    phone: str
    sms_code: str


class PhoneLoginIn(BaseModel):
    phone: str
    sms_code: str


class SmsCodeIn(BaseModel):
    phone: str
    scene: str = "login"


class PasswordLoginIn(BaseModel):
    username: str
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class LogoutIn(BaseModel):
    refresh_token: str | None = None


class PasswordChangeIn(BaseModel):
    old_password: str
    new_password: str


# ---------- 登录响应 ----------

def build_login_data(db: Session, user: User, request: Request | None) -> dict:
    projects, default_pid = user_projects(db, user)
    default_proj = db.get(Project, default_pid) if default_pid else None
    default_roles = next((p["roles"] for p in projects if p["id"] == default_pid), [])
    user_dict = user_to_dict(user, default_proj.name if default_proj else None)
    user_dict["roles"] = default_roles
    user_dict["project_id"] = default_pid
    return {
        "access_token": create_access_token(user, default_pid),
        "refresh_token": issue_refresh_token(db, user, request),
        "expires_in": settings.ACCESS_TOKEN_MINUTES * 60,
        "user": user_dict,
        "projects": projects,
        "must_change_password": user.must_change_password,
    }


# ---------- 账号密码登录 ----------

@router.post("/login")
def password_login(body: PasswordLoginIn, request: Request, db: Session = Depends(get_db)):
    """B 端/通用账号密码登录：限流 + 失败锁定 + 审计"""
    ip = _client_ip(request)
    ua = request.headers.get("user-agent")
    # 限流：同 IP 20 次/5min、同账号 5 次/5min
    ok_ip, retry_ip = hit(f"login:ip:{ip}", 20, 300)
    ok_acc, retry_acc = hit(f"login:acc:{body.username}", 5, 300)
    if not ok_ip or not ok_acc:
        retry = max(retry_ip, retry_acc)
        raise HTTPException(status_code=429, detail={"code": 429, "msg": "操作过于频繁，请稍后再试", "data": None},
                            headers={"Retry-After": str(retry)})

    user = db.query(User).filter(User.username == body.username).first()
    if user is None:
        write_audit(db, "login_failed", target_type="user", target_id=body.username, result="denied", ip=ip, user_agent=ua)
        raise BizError(1002, "用户名或密码错误")

    # 锁定检查
    if user.locked_until and now_utc() < _as_aware(user.locked_until):
        remain = int((_as_aware(user.locked_until) - now_utc()).total_seconds() // 60) + 1
        write_audit(db, "login_locked", actor_id=user.id, target_type="user", target_id=user.id, result="denied", ip=ip, user_agent=ua)
        raise BizError(1061, f"账号已锁定，请 {remain} 分钟后再试")

    if not user.active or user.status == "disabled":
        write_audit(db, "login_failed", actor_id=user.id, target_type="user", target_id=user.id, result="denied", ip=ip, user_agent=ua)
        raise BizError(1002, "用户名或密码错误")

    if not verify_password(body.password, user.password_hash):
        user.failed_login_count = (user.failed_login_count or 0) + 1
        if user.failed_login_count >= _LOCK_THRESHOLD:
            user.locked_until = now_utc() + timedelta(minutes=_LOCK_MINUTES)
            write_audit(db, "login_locked", actor_id=user.id, target_type="user", target_id=user.id, result="denied", ip=ip, user_agent=ua, commit=False)
            db.commit()
            raise BizError(1061, f"账号已锁定，请 {_LOCK_MINUTES} 分钟后再试")
        write_audit(db, "login_failed", actor_id=user.id, target_type="user", target_id=user.id, result="denied", ip=ip, user_agent=ua, commit=False)
        db.commit()
        # P1-1：预警剩余次数，避免"突然被锁=系统坏了"的误判
        remaining = _LOCK_THRESHOLD - user.failed_login_count
        raise BizError(1002, f"用户名或密码错误（再错 {remaining} 次将锁定 {_LOCK_MINUTES} 分钟）")

    # 成功：清零失败计数、记录登录时间
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now_utc()
    data = build_login_data(db, user, request)
    write_audit(db, "login_success", actor_id=user.id, target_type="user", target_id=user.id, ip=ip, user_agent=ua, commit=False)
    db.commit()
    return ok(data)


def _as_aware(dt):
    from .utils import as_utc
    return as_utc(dt)


# ---------- 令牌刷新 / 登出 ----------

@router.post("/refresh")
def refresh(body: RefreshIn, request: Request, db: Session = Depends(get_db)):
    """一次性轮换 refresh token；检测到已作废 token 复用 → 判定泄露，吊销该用户全部令牌"""
    token_hash = _sha256(body.refresh_token)
    row = db.query(RefreshToken).filter(RefreshToken.token_hash == token_hash).first()
    if row is None:
        raise _unauthorized()
    user = db.get(User, row.user_id)
    if user is None or not user.active or user.status == "disabled":
        raise _unauthorized()
    # 复用检测：已作废的 refresh 又被使用 → 泄露
    if row.revoked:
        revoke_all_refresh(db, user.id)
        user.token_version += 1
        write_audit(db, "token_reuse_detected", actor_id=user.id, target_type="user", target_id=user.id,
                    result="error", ip=_client_ip(request), user_agent=request.headers.get("user-agent"), commit=False)
        db.commit()
        raise _unauthorized("检测到令牌异常，请重新登录")
    if now_utc() >= _as_aware(row.expires_at):
        raise _unauthorized()
    # 数据库条件更新为唯一领取点；并发请求即使读到了相同快照也只有一个成功。
    claimed = db.query(RefreshToken).filter(
        RefreshToken.id == row.id, RefreshToken.revoked.is_(False),
        RefreshToken.expires_at > now_utc(),
    ).update({"revoked": True}, synchronize_session=False)
    if claimed != 1:
        db.rollback()
        raise _unauthorized("令牌已被使用，请使用最新登录凭证")
    # 领取、替代令牌与指针在同一事务中提交；签发失败将全部回滚。
    new_raw = issue_refresh_token(db, user, request)
    new_row = db.query(RefreshToken).filter(RefreshToken.token_hash == _sha256(new_raw)).first()
    row.replaced_by = new_row.id if new_row else None
    _, default_pid = user_projects(db, user)
    access = create_access_token(user, default_pid)
    db.commit()
    return ok({"access_token": access, "refresh_token": new_raw, "expires_in": settings.ACCESS_TOKEN_MINUTES * 60})


@router.post("/logout")
def logout(body: LogoutIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """作废当前 refresh token"""
    if body.refresh_token:
        row = db.query(RefreshToken).filter(RefreshToken.token_hash == _sha256(body.refresh_token),
                                            RefreshToken.user_id == user.id).first()
        if row and not row.revoked:
            row.revoked = True
    write_audit(db, "logout", actor_id=user.id, target_type="user", target_id=user.id,
                ip=_client_ip(request), user_agent=request.headers.get("user-agent"), commit=False)
    db.commit()
    return ok(None)


# ---------- 改密 ----------

@router.post("/password")
def change_password(body: PasswordChangeIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """改密：校验旧密码 + 新密码强度；改后吊销全部旧令牌并下发新令牌"""
    if not verify_password(body.old_password, user.password_hash):
        raise BizError(1002, "原密码错误")
    validate_password_strength(body.new_password)
    if verify_password(body.new_password, user.password_hash):
        raise BizError(1000, "新密码不能与原密码相同")
    user.password_hash = hash_password(body.new_password)
    user.password_updated_at = now_utc()
    user.must_change_password = False
    user.token_version += 1               # 吊销全部旧令牌
    revoke_all_refresh(db, user.id)
    write_audit(db, "password_changed", actor_id=user.id, target_type="user", target_id=user.id,
                ip=_client_ip(request), user_agent=request.headers.get("user-agent"), commit=False)
    data = build_login_data(db, user, request)  # 下发新令牌，避免用户被登出
    db.commit()
    return ok(data)


# ---------- 当前用户 ----------

@router.get("/me")
def me(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """当前用户 + 参与项目 + 当前项目角色/权限点列表"""
    from .deps import PERM_MATRIX, ProjectScope

    projects, default_pid = user_projects(db, user)
    # 当前项目：优先 X-Project-Id 头，其次默认项目
    raw = request.headers.get("x-project-id")
    cur_pid = None
    if raw and str(raw).strip():
        try:
            cur_pid = int(raw)
        except ValueError:
            cur_pid = None
    if cur_pid is None:
        cur_pid = default_pid
    if cur_pid is not None and cur_pid not in {p["id"] for p in projects}:
        raise HTTPException(status_code=403, detail={"code": 403, "msg": "无权访问该项目", "data": None})
    cur_proj = db.get(Project, cur_pid) if cur_pid else None
    cur_roles = next((p["roles"] for p in projects if p["id"] == cur_pid), [])

    data = user_to_dict(user, cur_proj.name if cur_proj else None)
    data["project_id"] = cur_pid
    data["roles"] = cur_roles
    data["must_change_password"] = user.must_change_password
    data["is_org_admin"] = user.is_org_admin
    data["projects"] = projects
    # 权限点列表（当前项目上下文下命中的 perm）
    scope = ProjectScope(user=user, project_id=cur_pid or 0, org_id=user.org_id or 0,
                         roles=set(cur_roles), is_org_admin=user.is_org_admin)
    data["perms"] = sorted(p for p in PERM_MATRIX if scope.has_perm(p))
    return ok(data)


# ---------- 短信验证码 ----------

@router.post("/sms-code")
def sms_code(body: SmsCodeIn, request: Request):
    """发送短信验证码：限流 60s/次、10 次/天；未配置短信服务 dev 返回 debug_code，prod 报错"""
    phone = body.phone.strip()
    if not phone:
        raise BizError(1000, "手机号不能为空")
    # 频率限制
    ok_freq, retry = hit(f"sms:freq:{phone}", 1, 60)
    if not ok_freq:
        raise HTTPException(status_code=429, detail={"code": 429, "msg": "发送过于频繁，请稍后再试", "data": None},
                            headers={"Retry-After": str(retry)})
    ok_day, _ = hit(f"sms:day:{phone}", 10, 86400)
    if not ok_day:
        raise BizError(1000, "今日验证码发送次数已达上限")

    code = f"{secrets.randbelow(1000000):06d}"
    set_kv(f"smscode:{body.scene}:{phone}", code, 300)

    sent, error = sms_channel.send_sms(phone, code)
    if sent:
        return ok({"sent": True})
    # 未配置：dev 返回 debug_code，prod 拒绝
    if settings.APP_ENV == "dev":
        logger.warning("短信未配置，dev 环境验证码 phone=%s code=%s", phone, code)
        return ok({"sent": False, "debug_code": code})
    del_kv(f"smscode:{body.scene}:{phone}")
    raise BizError(4012, "短信服务未配置，请使用微信一键登录")


# ---------- 微信登录 / 绑定 ----------

def _wx_code2session(code: str) -> dict:
    resp = httpx.get(
        "https://api.weixin.qq.com/sns/jscode2session",
        params={"appid": settings.WX_APPID, "secret": settings.WX_SECRET,
                "js_code": code, "grant_type": "authorization_code"},
        timeout=10,
    )
    return resp.json()


@router.post("/wx-login")
def wx_login(body: WxLoginIn, request: Request, db: Session = Depends(get_db)):
    """已绑定用户直接登录；首次登录创建隔离体验项目，无需手机验证码。"""
    allowed, _ = hit(f"wxlogin:{_client_ip(request)}", 30, 60)
    if not allowed: raise BizError(429, "登录过于频繁，请稍后再试")
    # 自建与云托管使用同一认证契约：请求头不构成可信入口证明。
    if not settings.WX_APPID or not settings.WX_SECRET:
        return {"code": 4001, "msg": "微信登录未配置", "data": None}
    if not body.code.strip():
        return {"code": 4002, "msg": "缺少微信登录凭证", "data": None}
    try:
        data = _wx_code2session(body.code)
    except Exception:  # noqa: BLE001
        return {"code": 4002, "msg": "微信登录失败", "data": None}
    openid = data.get("openid")
    if not openid:
        return {"code": 4002, "msg": "微信登录失败", "data": None}
    unionid = data.get("unionid")
    user = db.query(User).filter(User.wx_openid == openid).first()
    if user is None:
        from .services.wx_onboarding import create_trial_user
        user = create_trial_user(db, openid, unionid)
    if not user.active or user.status == "disabled":
        raise BizError(1002, "账号已停用")
    user.last_login_at = now_utc()
    result = build_login_data(db, user, request)
    write_audit(db, "login_success", actor_id=user.id, target_type="user", target_id=user.id,
                ip=_client_ip(request), user_agent=request.headers.get("user-agent"), commit=False)
    db.commit()
    return ok(result)


@router.post("/phone-login")
def phone_login(body: PhoneLoginIn, request: Request, db: Session = Depends(get_db)):
    """手机号独立登录，不建立或改写微信绑定。"""
    import re
    phone = body.phone.strip()
    if not re.fullmatch(r"1\d{10}", phone) or not re.fullmatch(r"\d{6}", body.sms_code):
        raise BizError(1000, "请输入正确的手机号和六位验证码")
    allowed, _ = hit(f"phonelogin:{phone}:{_client_ip(request)}", 5, 300)
    if not allowed: raise BizError(429, "验证过于频繁，请稍后再试")
    if not consume_kv(f"smscode:login:{phone}", body.sms_code):
        raise BizError(1000, "验证码错误或已过期")
    users = db.query(User).filter(User.phone == phone).limit(2).all()
    if len(users) != 1:
        raise BizError(4011, "手机号未登记或存在多个账号，请联系管理员")
    user = users[0]
    if not user.active or user.status == "disabled": raise BizError(1002, "账号已停用")
    user.last_login_at = now_utc()
    result = build_login_data(db, user, request)
    write_audit(db, "login_success", actor_id=user.id, target_type="user", target_id=user.id,
                ip=_client_ip(request), user_agent=request.headers.get("user-agent"), commit=False)
    db.commit()
    return ok(result)



@router.post("/wx-bind")
def wx_bind(body: WxBindIn, request: Request, db: Session = Depends(get_db)):
    """旧票据绑定流程已退役；保留明确错误，阻止遗留票据改写现有账号绑定。"""
    raise BizError(4010, "旧绑定流程已停用，请使用微信登录或手机号登录")
