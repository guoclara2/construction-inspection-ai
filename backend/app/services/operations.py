import hashlib
import json
import uuid
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
from sqlalchemy import update, or_, and_
from sqlalchemy.exc import IntegrityError
from ..database import SessionLocal
from ..config import settings
from ..models import Attachment, InspectionRecord, InspectionItem, Project, User, ProjectMember
from ..p1_models import AiJob, FeedbackRound, KnowledgeRevision, InspectionPlan, PlanTask
from ..utils import now_utc, as_utc, fmt, BizError, loads_safe

def fingerprint(body):
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def item_snapshot(item):
    return {k: (getattr(item, k).isoformat() if isinstance(getattr(item, k), datetime) else getattr(item, k)) for k in ('id', 'version', 'name', 'basis', 'check_points', 'defect_category',
        'publication', 'effective_from', 'effective_until', 'approved_by', 'risk_level', 'applicable_types', 'applicable_phases', 'default_sla_hours', 'enabled', 'updated_by')}

def freeze_item(db, item):
    snapshot = item_snapshot(item)
    if not db.query(KnowledgeRevision.id).filter_by(item_id=item.id, version=item.version).first():
        from .insert_once import insert_once
        db.execute(insert_once(KnowledgeRevision.__table__,
            dict(item_id=item.id, version=item.version, snapshot=snapshot),
            ['item_id', 'version'], db.bind.dialect.name))
    return snapshot

def revise_item(db, item, expected_version, values):
    """Claim the version before freezing its old contents, in one transaction."""
    if item.version != expected_version:
        raise BizError(1014, '标准版本已变化，请刷新后重试')
    old_snapshot = item_snapshot(item)
    result = db.execute(update(InspectionItem).where(InspectionItem.id == item.id,
        InspectionItem.version == expected_version).values(**values, version=expected_version + 1)
        .execution_options(synchronize_session=False))
    if result.rowcount != 1:
        db.rollback()
        raise BizError(1014, '标准版本已变化，请刷新后重试')
    from .insert_once import insert_once
    db.execute(insert_once(KnowledgeRevision.__table__,
        dict(item_id=item.id, version=expected_version, snapshot=old_snapshot),
        ['item_id', 'version'], db.bind.dialect.name))
    db.refresh(item)
    freeze_item(db, item)


def feedback_history(db, order):
    from ..routers.attachments import attachment_to_dict
    rows = db.query(FeedbackRound).filter_by(order_id=order.id).order_by(FeedbackRound.round).all()
    photos = db.query(Attachment).filter_by(biz_type='order_feedback', biz_id=order.id, deleted_at=None).all()
    result = [{"round": r.round, "text": r.text, "submitter_id": r.submitter_id,
        "submitted_at": fmt(r.submitted_at), "reviewer_id": r.reviewer_id,
        "reviewed_at": fmt(r.reviewed_at), "result": r.result, "comment": r.comment,
        "photos": [attachment_to_dict(a) for a in photos if a.round == r.round]} for r in rows]
    known = {r.round for r in rows}
    for number in sorted({a.round for a in photos if a.round is not None} - known):
        result.append({"round": number, "legacy": True,
            "text": order.feedback_text if number == order.round else "旧版本未保存本轮完整正文，请参考流转日志",
            "photos": [attachment_to_dict(a) for a in photos if a.round == number]})
    return sorted(result, key=lambda r: r['round'])

def enqueue(db, record):
    from .analyze import retryable_result
    retryable = retryable_result(loads_safe(record.ai_result))
    job = db.query(AiJob).filter_by(record_id=record.id).first()
    if job:
        if (job.status == 'failed' or (job.status == 'done' and retryable)) and job.attempts < 3 and record.human_verdict is None:
            job.status, job.error = 'pending', None
            db.commit()
        return job
    job = AiJob(record_id=record.id, status='done' if record.ai_result and not retryable else 'pending')
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        job = db.query(AiJob).filter_by(record_id=record.id).one()
    return job

def process_ai_jobs():
    """Durable CAS claim; bounded lease exceeds the model's total timeout."""
    from .analyze import analyze_record
    from .ai_meter import ai_scope
    with SessionLocal() as db:
        now = now_utc()
        eligible = or_(AiJob.status == 'pending', and_(AiJob.status == 'running', AiJob.lease_until < now))
        candidate = db.query(AiJob).filter(eligible, AiJob.attempts < 3).order_by(AiJob.id).first()
        if not candidate:
            db.execute(update(AiJob).where(AiJob.status == 'running', AiJob.lease_until < now,
                AiJob.attempts >= 3).values(status='failed', error='任务恢复次数已用尽，请人工判断'))
            db.commit()
            return 0
        jid, rid = candidate.id, candidate.record_id
        token = uuid.uuid4().hex
        if db.execute(update(AiJob).where(AiJob.id == jid, eligible).execution_options(synchronize_session=False).values(status='running',
            token=token, lease_until=now + timedelta(minutes=5), attempts=AiJob.attempts+1)).rowcount != 1:
            db.rollback()
            return 0
        db.commit()
        record = db.get(InspectionRecord, rid)
        status, error = 'done', None
        try:
            user, project = db.get(User, record.inspector_id), db.get(Project, record.project_id)
            member = db.query(ProjectMember.id).filter_by(project_id=record.project_id,
                user_id=record.inspector_id, role='inspector', active=True).first()
            if not user or not user.active or not project or project.status != 'active' or not member:
                raise ValueError('用户、项目或巡查权限已停用')
            if record.human_verdict is not None:
                status = 'cancelled'
            else:
                with ai_scope(record.project_id, record.inspector_id, project.org_id):
                    analyze_record(rid, db, retry=True)
        except Exception as exc:
            db.rollback()
            status, error = 'failed', ('AI任务失败，请重试或人工判断；' + type(exc).__name__)[:500]
        db.execute(update(AiJob).where(AiJob.id == jid, AiJob.token == token).values(
            status=status, error=error, lease_until=None))
        db.commit()
        return 1

def generate_tasks(db, batch_size=500):
    """Resume after each plan's last durable day; commit cursor and tasks together.

    Plans have immutable cadence/dates. The unique plan/day key protects retries;
    row locks serialize generators on server databases. No historical day is
    revisited and downtime backlogs drain in bounded batches without dropping days.
    """
    from .insert_once import insert_once
    today = now_utc().astimezone(ZoneInfo(settings.APP_TIMEZONE)).date()
    count = 0
    plans = db.query(InspectionPlan).filter(InspectionPlan.enabled.is_(True),
        InspectionPlan.start_date <= today).order_by(InspectionPlan.id)
    # Materialize small pages before issuing writes: MySQL server-side cursors
    # cannot safely interleave another statement on the same connection.
    def plan_pages():
        after = 0
        while True:
            page = plans.filter(InspectionPlan.id > after).limit(100).all()
            if not page:
                return
            after = page[-1].id
            yield from page
    for plan in plan_pages():
        # Current reads under MySQL REPEATABLE READ, after acquiring the plan lock.
        plan = db.query(InspectionPlan).filter_by(id=plan.id).populate_existing().with_for_update().one()
        if not plan.enabled:
            continue
        last = db.query(PlanTask.scheduled_date).filter_by(plan_id=plan.id).order_by(
            PlanTask.scheduled_date.desc()).with_for_update().first()
        day = last[0] + timedelta(days=plan.interval_days) if last else plan.start_date
        while day <= min(today, plan.end_date) and count < batch_size:
            due = datetime.combine(day + timedelta(days=1), time.min, ZoneInfo(settings.APP_TIMEZONE))
            db.execute(insert_once(PlanTask.__table__, dict(plan_id=plan.id, project_id=plan.project_id,
                inspector_id=plan.inspector_id, scheduled_date=day, due_at=as_utc(due), status='pending'),
                ['plan_id', 'scheduled_date'], db.bind.dialect.name))
            count += 1
            day += timedelta(days=plan.interval_days)
        if count >= batch_size:
            break
    db.flush()
    from .notify import notify
    missed = db.query(PlanTask).filter(PlanTask.status=='pending', PlanTask.due_at<=now_utc()).order_by(
        PlanTask.due_at, PlanTask.id).limit(batch_size).with_for_update().all()
    for task in missed:
        changed = db.execute(update(PlanTask).where(PlanTask.id==task.id, PlanTask.status=='pending')
            .values(status='missed')).rowcount
        if changed != 1:
            continue
        plan=db.get(InspectionPlan,task.plan_id)
        project=db.get(Project,task.project_id)
        notify(db,user_id=task.inspector_id,template_code='PLAN_TASK_MISSED',
            context={'plan_name':plan.name},project_id=task.project_id,org_id=project.org_id,
            biz_type='plan',biz_id=task.id,dedup_key=f'plan-missed:{task.id}')
    return count


def plan_tick():
    with SessionLocal() as db:
        count = generate_tasks(db)
        db.commit()
        return count

def complete_task(db, record):
    task = db.query(PlanTask).filter_by(record_id=record.id).first()
    if task:
        task.status = 'late' if as_utc(task.due_at) <= now_utc() else 'done'
        task.completed_at = now_utc()

def ensure_handoff(db, project_id, user_id, role):
    from ..models import RectifyOrder
    field = RectifyOrder.assignee_id if role == 'rectifier' else RectifyOrder.inspector_id
    if role in ('rectifier','inspector') and db.query(RectifyOrder.id).filter(
        RectifyOrder.project_id==project_id, field==user_id,
        RectifyOrder.status.notin_(('closed','cancelled'))).first():
        raise BizError(1014,'该成员有未闭环工单，请先在人员交接中移交')
    if role=='inspector' and db.query(PlanTask.id).filter(PlanTask.project_id==project_id,
        PlanTask.inspector_id==user_id,PlanTask.status.in_(('pending','missed'))).first():
        raise BizError(1014,'该成员有未完成计划任务，请先交接')
