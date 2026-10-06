"""Create a private trial workspace for a verified WeChat identity."""
import hashlib
import json
import secrets
from sqlalchemy.exc import IntegrityError
from ..models import Organization, User, Project, ProjectMember, InspectionItem, SlaRule
from ..security import hash_password


def create_trial_user(db, openid, unionid=None):
    # Stable unique account/organization keys prevent duplicate provisioning on retries.
    key = hashlib.sha256(openid.encode()).hexdigest()[:40]
    try:
        org = Organization(name='微信独立体验空间', code='wx-' + key)
        db.add(org); db.flush()
        user = User(username='wx-' + key, name='微信体验用户', org_id=org.id,
                    wx_openid=openid, wx_unionid=unionid, phone=None,
                    password_hash=hash_password(secrets.token_urlsafe(32)),
                    must_change_password=False, is_org_admin=True, active=True, status='active')
        db.add(user); db.flush()
        project = Project(org_id=org.id, name='我的独立体验项目', code='TRIAL',
                          project_type='building', phase='foundation', status='active')
        db.add(project); db.flush()
        for role in ('inspector', 'rectifier', 'reviewer', 'project_admin'):
            db.add(ProjectMember(project_id=project.id, user_id=user.id, role=role,
                                 is_default=role == 'inspector', active=True))
        # Seed static application templates only; never copy another tenant's records or standards.
        from ..seed import SEED_ITEMS
        for name, points, basis, category, risk, types, phases in SEED_ITEMS:
            db.add(InspectionItem(org_id=org.id, name=name, check_points=json.dumps(points, ensure_ascii=False),
                basis=basis, defect_category=category, risk_level=risk,
                applicable_types=json.dumps(types), applicable_phases=json.dumps(phases), enabled=True))
        for risk, hours, escalate in (('high',8,4),('medium',48,24),('low',168,48)):
            db.add(SlaRule(org_id=org.id, risk_level=risk, sla_hours=hours,
                escalate_after_hours=escalate, escalate_to_role='project_admin', enabled=True))
        db.flush()
        return user
    except IntegrityError:
        db.rollback()
        existing = db.query(User).filter_by(wx_openid=openid).first()
        if existing is None: raise
        return existing
