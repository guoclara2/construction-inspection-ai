"""Explicit one-time production bootstrap; no seeded credentials and no password logging."""
import getpass
from app.database import SessionLocal
from app.models import Organization, User, Project, ProjectMember
from app.security import hash_password, validate_password_strength

def main():
    with SessionLocal() as db:
        if db.query(User.id).first():
            raise SystemExit('已有账号，禁止重复初始化；请通过管理员管理账号')
        organization = input('组织名称: ').strip()
        project_name = input('首个项目名称: ').strip()
        username = input('管理员登录名: ').strip()
        password = getpass.getpass('管理员初始强密码: ')
        if password != getpass.getpass('再次输入密码: '):
            raise SystemExit('两次密码不一致')
        validate_password_strength(password)
        if not organization or not project_name or not username:
            raise SystemExit('名称不可为空')
        org = Organization(name=organization, code='INITIAL')
        db.add(org); db.flush()
        user = User(username=username, name=username, password_hash=hash_password(password),
                    org_id=org.id, is_org_admin=True, must_change_password=True)
        db.add(user); db.flush()
        project = Project(org_id=org.id, name=project_name, code='P1', project_type='building', phase='foundation')
        db.add(project); db.flush()
        db.add(ProjectMember(project_id=project.id,user_id=user.id,role='project_admin',is_default=True))
        db.commit()
    print('初始化完成。首次登录必须改密；请核对项目类型和阶段并录入正式检查项。')

if __name__ == '__main__':
    main()
