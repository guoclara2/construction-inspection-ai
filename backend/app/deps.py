# 项目上下文与权限依赖：多租户隔离的唯一注入点（总纲 §5.0 / §6.1）
# 所有业务查询的 project_id 过滤必须来自 project_scope 注入的值，禁止裸查询、禁止从请求参数直取 project_id。
import logging
from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Query, Session

from .auth import get_current_user  # 令牌解析（P02）；deps 作为路由统一依赖入口对外暴露
from .database import get_db
from .models import Project, ProjectMember, User
from .services.audit import write_audit
from .utils import BizError

logger = logging.getLogger(__name__)

__all__ = [
    "ProjectScope",
    "get_current_user",
    "get_current_project_id",
    "get_membership",
    "project_scope",
    "require_perm",
    "scoped_query",
    "PERM_MATRIX",
]


# ---------- 权限矩阵（总纲 §6.1，声明式校验的唯一事实源） ----------
# perm -> 允许的角色集合。角色：inspector/rectifier/reviewer/project_admin/viewer/org_admin
# 注：带「本人」「仅被指派」限定的权限，本矩阵只做粗粒度角色门槛，归属判定仍在各 handler 内完成。
PERM_MATRIX: dict[str, set[str]] = {
    "record:create": {"inspector"},
    "record:analyze": {"inspector"},
    "record:confirm": {"inspector"},
    "record:read:own": {"inspector"},
    "record:read:all": {"reviewer", "project_admin", "org_admin", "viewer"},
    "order:accept": {"rectifier"},
    "order:start": {"rectifier"},
    "order:feedback": {"rectifier"},
    "order:review": {"inspector", "reviewer"},
    "order:transfer": {"inspector", "project_admin", "org_admin"},
    "order:cancel": {"inspector", "project_admin", "org_admin"},
    "order:extend:apply": {"rectifier"},
    "order:extend:approve": {"inspector", "project_admin", "org_admin"},
    "order:urge": {"project_admin", "org_admin"},
    "knowledge:write": {"project_admin", "org_admin"},
    "plan:write": {"project_admin", "org_admin"},
    "sla:write": {"project_admin", "org_admin"},
    "member:write": {"project_admin", "org_admin"},
    "audit:read": {"project_admin", "org_admin"},
    "export": {"project_admin", "org_admin"},
    "org:manage": {"org_admin"},
}

READ_ALL_ROLES = {"reviewer", "project_admin", "org_admin", "viewer"}


@dataclass
class ProjectScope:
    """一次请求的项目上下文：所有隔离查询的过滤依据"""

    user: User
    project_id: int
    org_id: int
    roles: set[str] = field(default_factory=set)   # 本项目内的成员角色集合
    is_org_admin: bool = False
    memberships: list[ProjectMember] = field(default_factory=list)

    @property
    def effective_roles(self) -> set[str]:
        """有效角色 = 项目成员角色 + （组织管理员则附加 org_admin）"""
        roles = set(self.roles)
        if self.is_org_admin:
            roles.add("org_admin")
        return roles

    def has_perm(self, perm: str) -> bool:
        allowed = PERM_MATRIX.get(perm, set())
        return bool(self.effective_roles & allowed)

    @property
    def can_read_all(self) -> bool:
        """是否可查看本项目全部数据（非仅本人）"""
        return bool(self.effective_roles & READ_ALL_ROLES)


def get_current_project_id(request: Request, user: User = Depends(get_current_user)) -> int:
    """解析当前项目 id：优先请求头 X-Project-Id，其次 JWT 的 pid；皆无 → 1030 未选择项目"""
    raw = request.headers.get("x-project-id")
    if raw is None or str(raw).strip() == "":
        raw = getattr(request.state, "jwt_pid", None)
    if raw is None or str(raw).strip() == "":
        raise BizError(1030, "未选择项目")
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise BizError(1030, "未选择项目")


def get_membership(user: User, project_id: int, db: Session) -> list[ProjectMember]:
    """当前用户在指定项目内的有效成员关系（可能多行=多角色）"""
    return (
        db.query(ProjectMember)
        .filter(
            ProjectMember.user_id == user.id,
            ProjectMember.project_id == project_id,
            ProjectMember.active.is_(True),
        )
        .all()  # 结果集有界：单用户单项目角色行（≤ 角色枚举基数）
    )


def project_scope(
    request: Request,
    user: User = Depends(get_current_user),
    project_id: int = Depends(get_current_project_id),
    db: Session = Depends(get_db),
) -> ProjectScope:
    """构造项目上下文：非本项目成员（且非同组织 org_admin）→ HTTP 403 并写审计"""
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail={"code": 404, "msg": "项目不存在", "data": None})

    memberships = get_membership(user, project_id, db)
    roles = {m.role for m in memberships}
    is_org_admin_here = bool(user.is_org_admin and user.org_id is not None and project.org_id == user.org_id)

    if not memberships and not is_org_admin_here:
        # 越权访问非本人项目：一律 403 + 审计（不得静默）
        write_audit(
            db, "project_access_denied", actor_id=user.id, target_type="project", target_id=project_id,
            result="denied", org_id=project.org_id, project_id=project_id,
            ip=_client_ip(request), user_agent=request.headers.get("user-agent"),
        )
        raise HTTPException(status_code=403, detail={"code": 403, "msg": "无权访问该项目", "data": None})

    return ProjectScope(
        user=user,
        project_id=project_id,
        org_id=project.org_id,
        roles=roles,
        is_org_admin=is_org_admin_here,
        memberships=memberships,
    )


def require_perm(*perms: str):
    """声明式权限校验依赖（总纲 §6.1）。任一 perm 命中即放行；否则 HTTP 403 + 审计 denied。"""

    def _dep(
        request: Request,
        scope: ProjectScope = Depends(project_scope),
        db: Session = Depends(get_db),
    ) -> ProjectScope:
        if any(scope.has_perm(p) for p in perms):
            return scope
        write_audit(
            db, "perm_denied", actor_id=scope.user.id, target_type="perm", target_id=",".join(perms),
            result="denied", org_id=scope.org_id, project_id=scope.project_id,
            ip=_client_ip(request), user_agent=request.headers.get("user-agent"),
        )
        raise HTTPException(status_code=403, detail={"code": 403, "msg": "无权限", "data": None})

    return _dep


def scoped_query(db: Session, model, scope: ProjectScope) -> Query:
    """统一取隔离查询：强制注入 project_id 过滤，业务代码一律通过它构建 query。"""
    return db.query(model).filter(model.project_id == scope.project_id)


def _client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None
