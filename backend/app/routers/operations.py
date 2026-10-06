from datetime import date, datetime, timedelta
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import update
from sqlalchemy.orm import Session
from ..database import get_db
from ..deps import project_scope, require_perm, ProjectScope
from ..models import InspectionRecord, InspectionItem, RectifyOrder, ProjectMember, User, OrderLog, Attachment
from ..p1_models import AiJob, AiBudget, AiUsage, InspectionPlan, PlanTask, DeadlineRequest, KnowledgeRevision
from ..utils import ok, BizError, forbidden, fmt, now_utc, as_utc
from ..services.operations import enqueue, generate_tasks, freeze_item
from ..config import settings

router = APIRouter(prefix='/api', tags=['operations'])

def record_owned(db, rid, scope):
    r = db.get(InspectionRecord, rid)
    if not r or r.project_id != scope.project_id or r.inspector_id != scope.user.id:
        raise forbidden('记录不存在或无权限')
    return r

def job_dict(job, record):
    from .inspection import record_to_dict
    return {'id': job.id, 'status': job.status, 'attempts': job.attempts, 'error': job.error,
            'stage': {'pending': '排队中', 'running': '分析中', 'done': '分析完成',
                      'failed': '失败，可重试或人工判断', 'cancelled': '已人工确认'}.get(job.status),
            'record': record_to_dict(record, detail=True)}

@router.post('/inspection-records/{rid}/analysis-job')
def create_job(rid: int, db: Session=Depends(get_db), scope: ProjectScope=Depends(require_perm('record:analyze'))):
    record = record_owned(db, rid, scope)
    if record.human_verdict is not None:
        raise BizError(1012, '记录已经确认')
    if not settings.SCHEDULER_ENABLED:
        raise BizError(503, '后台任务未启用，请联系管理员或直接人工判断')
    return ok(job_dict(enqueue(db, record), record))

@router.get('/inspection-records/{rid}/analysis-job')
def get_job(rid: int, db: Session=Depends(get_db), scope: ProjectScope=Depends(require_perm('record:analyze'))):
    record = record_owned(db, rid, scope)
    job = db.query(AiJob).filter_by(record_id=rid).first()
    if not job: raise BizError(404, '尚未创建分析任务')
    return ok(job_dict(job, record))

class PlanIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    item_id: int
    inspector_id: int
    start_date: date
    end_date: date
    interval_days: int = Field(default=1, ge=1, le=365)

@router.post('/plans')
def create_plan(body: PlanIn, db: Session=Depends(get_db), scope: ProjectScope=Depends(require_perm('plan:write'))):
    if body.end_date < body.start_date or (body.end_date-body.start_date).days > 366:
        raise BizError(1000, '计划周期需在1至367天内')
    item = db.get(InspectionItem, body.item_id)
    user = db.get(User, body.inspector_id)
    member = db.query(ProjectMember.id).filter_by(project_id=scope.project_id, user_id=body.inspector_id,
        role='inspector', active=True).first()
    if not item or not item.enabled or item.org_id not in (None, scope.org_id) or not member or not user or not user.active:
        raise BizError(1000, '检查项或巡查员不属于本项目可用范围')
    plan = InspectionPlan(project_id=scope.project_id, **body.model_dump())
    db.add(plan); db.flush(); generate_tasks(db); db.commit()
    return ok({'id': plan.id})

@router.get('/plans')
def plans(db: Session=Depends(get_db), scope: ProjectScope=Depends(require_perm('plan:write'))):
    rows = db.query(InspectionPlan).filter_by(project_id=scope.project_id).order_by(InspectionPlan.id.desc()).limit(500).all()
    return ok([{'id': p.id, 'name': p.name, 'item_id': p.item_id, 'inspector_id': p.inspector_id,
        'start_date': str(p.start_date), 'end_date': str(p.end_date), 'interval_days': p.interval_days,
        'enabled': p.enabled} for p in rows])

@router.post('/plans/{pid}/disable')
def disable_plan(pid: int, db: Session=Depends(get_db), scope: ProjectScope=Depends(require_perm('plan:write'))):
    plan = db.get(InspectionPlan, pid)
    if not plan or plan.project_id != scope.project_id: raise forbidden('无权限')
    plan.enabled=False
    db.execute(update(PlanTask).where(PlanTask.plan_id==pid, PlanTask.status=='pending',
        PlanTask.record_id.is_(None)).values(status='cancelled'))
    db.commit(); return ok({'id': pid, 'enabled': False})

@router.get('/plan-tasks')
def tasks(db: Session=Depends(get_db), scope: ProjectScope=Depends(project_scope)):
    q = db.query(PlanTask).filter_by(project_id=scope.project_id)
    if not scope.can_read_all: q=q.filter_by(inspector_id=scope.user.id)
    rows=q.order_by(PlanTask.scheduled_date.desc(), PlanTask.id.desc()).limit(500).all()
    counts={key:q.filter(PlanTask.status==key).count() for key in ('pending','missed','done','late','cancelled')}
    expected=sum(counts[k] for k in ('pending','missed','done','late'))
    return ok({'list':[{'id':t.id,'plan_name': db.get(InspectionPlan,t.plan_id).name,
        'item_id':db.get(InspectionPlan,t.plan_id).item_id, 'inspector_id':t.inspector_id,
        'scheduled_date':str(t.scheduled_date), 'due_at':fmt(t.due_at), 'status':t.status,
        'record_id':t.record_id} for t in rows], 'counts':counts,
        'coverage': (counts['done']+counts['late'])/expected if expected else None,
        'on_time_rate':counts['done']/expected if expected else None})

class ExtensionIn(BaseModel):
    deadline: datetime
    reason: str = Field(min_length=1,max_length=2000)
    expected_version: int

@router.post('/orders/{oid}/extensions')
def apply_extension(oid:int, body:ExtensionIn, db:Session=Depends(get_db), scope:ProjectScope=Depends(require_perm('order:extend:apply'))):
    from .orders import _get_scoped_order
    order=_get_scoped_order(oid,db,scope)
    if order.assignee_id != scope.user.id: raise forbidden('仅整改责任人可申请')
    if order.status in ('closed','cancelled') or order.version != body.expected_version:
        raise BizError(1014,'工单已变化，请刷新')
    if not body.deadline.tzinfo or not body.reason.strip() or not as_utc(order.deadline)<body.deadline<=now_utc()+timedelta(days=90):
        raise BizError(1000,'请填写原因及晚于原期限、90天内且含时区的新期限')
    if db.query(DeadlineRequest.id).filter_by(order_id=oid,status='pending').first():
        raise BizError(1014,'已有待审批延期申请')
    if db.execute(update(RectifyOrder).where(RectifyOrder.id==oid,RectifyOrder.version==body.expected_version)
        .values(version=RectifyOrder.version+1)).rowcount!=1: raise BizError(1014,'工单已变化')
    row=DeadlineRequest(order_id=oid,requester_id=scope.user.id,deadline=body.deadline,reason=body.reason.strip())
    db.add(row); db.commit(); return ok({'id':row.id})

@router.get('/orders/{oid}/extensions')
def extensions(oid:int, db:Session=Depends(get_db), scope:ProjectScope=Depends(project_scope)):
    from .orders import _get_scoped_order,_visible
    order=_get_scoped_order(oid,db,scope)
    if not _visible(order,scope): raise forbidden('无权限')
    return ok([{'id':r.id,'deadline':fmt(r.deadline),'reason':r.reason,'status':r.status,
        'requester_id':r.requester_id,'reviewer_id':r.reviewer_id,'comment':r.comment}
        for r in db.query(DeadlineRequest).filter_by(order_id=oid).order_by(DeadlineRequest.id).all()])

class ApprovalIn(BaseModel):
    approve: bool
    comment: str = Field(min_length=1,max_length=500)
    expected_version: int

@router.post('/orders/{oid}/extensions/{eid}/review')
def review_extension(oid:int,eid:int,body:ApprovalIn, db:Session=Depends(get_db), scope:ProjectScope=Depends(require_perm('order:extend:approve'))):
    from .orders import _get_scoped_order
    order=_get_scoped_order(oid,db,scope)
    row=db.get(DeadlineRequest,eid,with_for_update=True)
    if not row or row.order_id!=oid or row.status!='pending': raise BizError(1014,'申请已处理或不存在')
    if scope.user.id in (row.requester_id,order.assignee_id): raise forbidden('不可审批本人申请或负责的整改')
    if order.inspector_id!=scope.user.id and not scope.has_perm('plan:write'): raise forbidden('仅发起人或管理员可审批')
    if not body.comment.strip(): raise BizError(1000,'请填写审批意见')
    values={'version':RectifyOrder.version+1}
    if body.approve:
        if as_utc(row.deadline)<=max(now_utc(),as_utc(order.deadline)): raise BizError(1014,'申请期限已失效')
        values.update(deadline=row.deadline,warn_at=now_utc()+(as_utc(row.deadline)-now_utc())*0.8)
    if db.execute(update(RectifyOrder).where(RectifyOrder.id==oid,RectifyOrder.version==body.expected_version,
        RectifyOrder.status.notin_(('closed','cancelled'))).values(**values)).rowcount!=1:
        raise BizError(1014,'工单已变化，请刷新')
    row.status='approved' if body.approve else 'rejected'; row.reviewer_id=scope.user.id; row.comment=body.comment
    db.add(OrderLog(order_id=oid,from_status=order.status,to_status=order.status,operator_id=scope.user.id,
        action='extend',remark=f"延期{row.status}: {fmt(row.deadline)}；{body.comment}"[:500]))
    db.commit(); return ok({'status':row.status})

@router.get('/operations/ai-usage')
def usage(db:Session=Depends(get_db),scope:ProjectScope=Depends(require_perm('audit:read'))):
    budget=db.get(AiBudget,(f'project:{scope.project_id}',now_utc().date()))
    used=budget.calls if budget else 0
    rows=db.query(AiUsage).filter_by(project_id=scope.project_id).order_by(AiUsage.id.desc()).limit(200).all()
    return ok({'day_utc':str(now_utc().date()),'used':used,'limit':settings.AI_DAILY_QUOTA,
        'alert':used>=settings.AI_DAILY_QUOTA*0.8,'user_limit':settings.AI_USER_DAILY_QUOTA,
        'concurrency_limit':settings.AI_PROJECT_CONCURRENCY,
        'list':[{'id':r.id,'user_id':r.user_id,'purpose':r.purpose,'model':r.model,'status':r.status,
        'input_tokens':r.input_tokens,'output_tokens':r.output_tokens,'cost_cny':r.cost,'created_at':fmt(r.created_at)} for r in rows]})

@router.get('/knowledge/{iid}/versions')
def versions(iid:int,db:Session=Depends(get_db),scope:ProjectScope=Depends(require_perm('knowledge:write'))):
    item=db.get(InspectionItem,iid)
    if not item or item.org_id not in (None,scope.org_id): raise forbidden('无权限')
    freeze_item(db,item);db.commit()
    return ok([{'version':r.version,'snapshot':r.snapshot,'created_at':fmt(r.created_at)}
        for r in db.query(KnowledgeRevision).filter_by(item_id=iid).order_by(KnowledgeRevision.version.desc()).all()])

class PublishIn(BaseModel):
    expected_version: int
    effective_from: datetime
    effective_until: datetime

@router.post('/knowledge/{iid}/publish')
def publish(iid:int,body:PublishIn,db:Session=Depends(get_db),scope:ProjectScope=Depends(require_perm('knowledge:write'))):
    item=db.get(InspectionItem,iid,with_for_update=True)
    if not item or item.org_id!=scope.org_id: raise forbidden('仅可发布本组织标准')
    if item.updated_by==scope.user.id: raise forbidden('发布审核人须与本版本编辑人不同')
    if not body.effective_from.tzinfo or not body.effective_until.tzinfo or body.effective_until<=body.effective_from:
        raise BizError(1000,'请填写含时区的有效起止时间')
    if item.version!=body.expected_version: raise BizError(1014,'标准版本已变化')
    freeze_item(db,item)
    if db.execute(update(InspectionItem).where(InspectionItem.id==iid,InspectionItem.version==body.expected_version)
        .values(version=InspectionItem.version+1,publication='published',enabled=True,approved_by=scope.user.id,
            effective_from=body.effective_from,effective_until=body.effective_until)).rowcount!=1:
        raise BizError(1014,'标准版本已变化')
    db.refresh(item); freeze_item(db,item); db.commit()
    return ok({'version':item.version,'publication':item.publication})

class EvidenceReviewIn(BaseModel):
    comment: str = Field(min_length=1,max_length=1000)

@router.post('/attachments/{aid}/evidence-review')
def evidence_review(aid:int,body:EvidenceReviewIn,db:Session=Depends(get_db),scope:ProjectScope=Depends(project_scope)):
    from .attachments import _get_scoped_attachment, attachment_to_dict
    att=_get_scoped_attachment(aid,db,scope)
    if not scope.effective_roles.intersection({'inspector','reviewer','project_admin','org_admin'}):
        raise forbidden('仅巡查或审核人员可记录存证复核')
    if not body.comment.strip(): raise BizError(1000,'请填写复核说明')
    # Row lock protects append history on PostgreSQL.
    att=db.get(Attachment,aid,with_for_update=True,populate_existing=True)
    data=dict(att.evidence or {'legacy':True})
    data['reviews']=[*data.get('reviews',[]),{'user_id':scope.user.id,'time':now_utc().isoformat(),'comment':body.comment}]
    att.evidence=data;db.commit();return ok(attachment_to_dict(att))

class HandoffIn(BaseModel):
    from_user: int
    to_user: int
    role: str
    reason: str=Field(min_length=1,max_length=500)

@router.post('/operations/handoff')
def handoff(body:HandoffIn,db:Session=Depends(get_db),scope:ProjectScope=Depends(require_perm('member:write'))):
    if body.role not in ('inspector','rectifier') or body.from_user==body.to_user or not body.reason.strip():
        raise BizError(1000,'请选择不同的交接人员，角色及交接原因')
    target=db.get(User,body.to_user)
    if not target or not target.active or not db.query(ProjectMember.id).filter_by(
        project_id=scope.project_id,user_id=body.to_user,role=body.role,active=True).first():
        raise BizError(1000,'接替人必须是本项目具有相同角色的启用成员')
    field=RectifyOrder.inspector_id if body.role=='inspector' else RectifyOrder.assignee_id
    orders=db.query(RectifyOrder).filter(RectifyOrder.project_id==scope.project_id,field==body.from_user,
        RectifyOrder.status.notin_(('closed','cancelled'))).with_for_update().all()
    count=0
    for order in orders:
        if body.to_user in (order.assignee_id if body.role=='inspector' else order.inspector_id,):
            raise BizError(1014,'交接会导致同一人负责整改和审核，请更换接替人')
        values={field.key:body.to_user,'version':RectifyOrder.version+1}
        if body.role=='rectifier' and order.status=='review':
            raise BizError(1014,'原责任人仍有待审核反馈，请完成审核后再交接')
        if db.execute(update(RectifyOrder).where(RectifyOrder.id==order.id,RectifyOrder.version==order.version)
            .values(**values)).rowcount!=1:raise BizError(1014,'工单发生并发变化，请刷新后重试')
        db.add(OrderLog(order_id=order.id,from_status=order.status,to_status=order.status,operator_id=scope.user.id,
            action='handoff',remark=f'{body.role}: {body.from_user}→{body.to_user}；{body.reason}'[:500]))
        from ..services.notify import notify
        notify(db,user_id=body.to_user,template_code='ORDER_TRANSFER',context={'order_no':order.order_no,
            'new_assignee_name':target.name,'reason':body.reason},project_id=scope.project_id,org_id=scope.org_id,
            biz_type='order',biz_id=order.id,dedup_key=f'handoff:{order.id}:{body.role}:{order.version}')
        count+=1
    if body.role=='inspector':
        active=db.query(PlanTask).filter(PlanTask.project_id==scope.project_id,PlanTask.inspector_id==body.from_user,
            PlanTask.status.in_(('pending','missed'))).all()
        if any(t.record_id for t in active): raise BizError(1014,'存在已拍照未确认的计划记录，请由原巡查员完成确认后再交接')
        for task in active:task.inspector_id=body.to_user
        db.execute(update(InspectionPlan).where(InspectionPlan.project_id==scope.project_id,
            InspectionPlan.inspector_id==body.from_user,InspectionPlan.enabled.is_(True)).values(inspector_id=body.to_user))
    db.commit();return ok({'orders':count})
