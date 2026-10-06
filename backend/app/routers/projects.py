# 项目上下文路由：我的项目列表 / 切换当前项目（总纲 §5.2）
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..auth import create_access_token, get_current_user, user_projects
from ..config import settings
from ..database import get_db
from ..deps import get_membership
from ..models import Project, User
from ..utils import BizError, forbidden, ok

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("")
def my_projects(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """我参与的项目列表 [{id,name,code,roles,org_unit,is_default}]"""
    projects, default_pid = user_projects(db, user)
    return ok({"list": projects, "default_project_id": default_pid})


@router.post("/{project_id}/switch")
def switch_project(project_id: int, request: Request, user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    """切换当前项目：校验成员资格后签发 pid 更新的新 access_token（refresh 不变）"""
    project = db.get(Project, project_id)
    if project is None:
        raise BizError(1023, "项目不存在")
    memberships = get_membership(user, project_id, db)
    is_org_admin_here = bool(user.is_org_admin and user.org_id is not None and project.org_id == user.org_id)
    if not memberships and not is_org_admin_here:
        raise forbidden("无权访问该项目")
    access = create_access_token(user, project_id)
    roles = [m.role for m in memberships] or (["project_admin"] if is_org_admin_here else [])
    return ok({
        "access_token": access,
        "expires_in": settings.ACCESS_TOKEN_MINUTES * 60,
        "project": {"id": project.id, "name": project.name, "code": project.code, "roles": roles},
    })
