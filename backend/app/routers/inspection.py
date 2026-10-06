# 巡查模块路由：检查项查询、巡查记录（全程按 project_scope 强制隔离）
# P04：图片上传迁移至 /api/attachments（证据链），本模块不再提供上传接口
# P05：confirm 幂等（1012 + Idempotency-Key）+ 行锁串行化 + 序列取号 + SLA 时限 + 通知后置
import json
import logging

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import ProjectScope, project_scope, require_perm
from ..models import AiFeedback, Attachment, InspectionItem, InspectionRecord, OrderLog, Project, ProjectMember, RectifyOrder, User
from ..schemas import RecordConfirm, RecordCreate
from ..services.operations import fingerprint, freeze_item, complete_task
from ..services.ai_meter import ai_scope
from ..services.analyze import FALLBACK_RESULT, analyze_record, is_actionable_ai_result
from ..services.notify import dispatch_pending, notify
from ..services.order_flow import next_order_no
from ..services.recommend import recommend_items
from ..services.sla import compute_deadline, compute_warn_at, resolve_sla_hours
from ..utils import BizError, fmt, forbidden, loads_safe, now_utc, ok
from .attachments import attachment_to_dict, bind_attachment, load_bindable_attachment

logger = logging.getLogger(__name__)

router = APIRouter(tags=["inspection"])  # 巡查记录路由（无统一前缀）


def item_to_dict(item: InspectionItem) -> dict:
    """检查项序列化：JSON text 字段解析为数组"""
    return {
        "id": item.id,
        "org_id": item.org_id,
        "version": item.version, "publication": item.publication,
        "effective_from": fmt(item.effective_from), "effective_until": fmt(item.effective_until),
        "name": item.name,
        "check_points": json.loads(item.check_points) if item.check_points else [],
        "basis": item.basis,
        "defect_category": item.defect_category,
        "risk_level": item.risk_level,
        "applicable_types": json.loads(item.applicable_types) if item.applicable_types else [],
        "applicable_phases": json.loads(item.applicable_phases) if item.applicable_phases else [],
        "enabled": item.enabled,
        "created_at": fmt(item.created_at),
    }


def item_org_filter(org_id: int | None):
    """知识库组织过滤：本组织条目 + 平台内置模板（org_id IS NULL）"""
    return or_(InspectionItem.org_id == org_id, InspectionItem.org_id.is_(None))


@router.get('/api/inspection-items/{item_id}')
def item_detail(item_id:int,db:Session=Depends(get_db),scope:ProjectScope=Depends(project_scope)):
    item=db.get(InspectionItem,item_id)
    if not item or not item.enabled or item.org_id not in (None,scope.org_id):
        raise BizError(1007,'检查项不存在或已停用')
    return ok(item_to_dict(item))


def build_item_query(
    db: Session,
    keyword: str | None,
    project_type: str | None,
    phase: str | None,
    enabled_param: str | None,
    org_id: int | None,
):
    """公共过滤逻辑：org 隔离 / keyword / 类型阶段 JSON LIKE / enabled"""
    q = db.query(InspectionItem).filter(item_org_filter(org_id))
    if keyword:
        # 契约：keyword 仅模糊匹配名称，避免依据条款误命中
        q = q.filter(InspectionItem.name.like(f"%{keyword}%"))
    if project_type:
        # JSON 数组文本匹配需含引号，避免 building 误匹配
        q = q.filter(InspectionItem.applicable_types.like(f'%"{project_type}"%'))
    if phase:
        q = q.filter(InspectionItem.applicable_phases.like(f'%"{phase}"%'))
    if enabled_param == "all":
        pass  # 仅 admin 使用，见 admin 路由
    elif enabled_param in ("false", "0"):
        q = q.filter(InspectionItem.enabled.is_(False))
    else:
        q = q.filter(InspectionItem.enabled.is_(True),
            or_(InspectionItem.effective_from.is_(None), InspectionItem.effective_from <= now_utc()),
            or_(InspectionItem.effective_until.is_(None), InspectionItem.effective_until > now_utc()))
    return q


@router.get("/api/inspection-items")
def list_items(
    keyword: str | None = None,
    project_type: str | None = None,
    phase: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    scope: ProjectScope = Depends(project_scope),
):
    """检查项列表：按当前项目所属组织隔离；非 admin 只见启用项"""
    q = build_item_query(db, keyword, project_type, phase, None, scope.org_id)
    total = q.count()
    items = q.order_by(InspectionItem.id).offset((page - 1) * page_size).limit(page_size).all()
    return ok({"list": [item_to_dict(i) for i in items], "total": total, "page": page, "page_size": page_size})


class RecommendIn(BaseModel):
    project_type: str
    phase: str
    text: str | None = None


@router.post("/api/inspection-items/recommend")
def recommend(body: RecommendIn, db: Session = Depends(get_db),
              scope: ProjectScope = Depends(require_perm("record:create"))):
    """事项推荐：结构化匹配 + 语义补充（仅 inspector），知识库按 org 隔离"""
    with ai_scope(scope.project_id, scope.user.id, scope.org_id):
        results = recommend_items(body.project_type, body.phase, body.text, db, scope.org_id)
    return ok([{"item": r["item"], "reason": r["reason"]} for r in results])


def _get_scoped_record(record_id: int, db: Session, scope: ProjectScope) -> InspectionRecord:
    """取记录并强制校验属于当前项目（不存在或跨项目一律 1009）"""
    record = db.get(InspectionRecord, record_id)
    if record is None or record.project_id != scope.project_id:
        raise BizError(1009, "巡查记录不存在")
    return record


@router.post("/api/inspection-records/{record_id}/analyze")
def analyze(record_id: int, db: Session = Depends(get_db),
            scope: ProjectScope = Depends(require_perm("record:analyze"))):
    """触发 AI 判别：记录须属本项目且为本人"""
    record = _get_scoped_record(record_id, db, scope)
    if record.inspector_id != scope.user.id:
        raise forbidden("无权限")
    from ..config import settings
    if settings.is_prod and settings.AI_PRODUCTION_ENABLED:
        raise BizError(1000, "请使用异步 analysis-job 接口")
    try:
        with ai_scope(scope.project_id, scope.user.id, scope.org_id):
            result, mode = analyze_record(record_id, db)
    except ValueError as e:
        raise BizError(1009, str(e))
    # P0-4：actionable=判别结果可采信（置信度达标且非无效结论），前端据此决定是否预填工单草稿
    return ok({"ai_result": result, "mode": mode, "actionable": is_actionable_ai_result(result)})


@router.get("/api/inspection-records/{record_id}/draft")
def preview_draft(record_id: int, db: Session = Depends(get_db),
                  scope: ProjectScope = Depends(require_perm("record:confirm"))):
    record = _get_scoped_record(record_id, db, scope)
    if record.inspector_id != scope.user.id:
        raise forbidden("无权限")
    if record.human_verdict is not None:
        raise BizError(1012, "记录已确认")
    item = db.get(InspectionItem, record.item_id)
    hours, _ = resolve_sla_hours(item, item.risk_level if item else "medium",
                                 record.defect_category, scope.project_id, db)
    ai = loads_safe(record.ai_result) or {}
    usable = is_actionable_ai_result(ai)
    return ok({"problem_location": f"{record.item_name}：{ai.get('defect_desc', '')}" if usable else "",
               "rectify_requirement": ai.get("suggestion", "") if usable else "",
               "basis": record.item_basis, "sla_hours": hours})


@router.post("/api/inspection-records/{record_id}/confirm")
def confirm_record(record_id: int, body: RecordConfirm, request: Request, db: Session = Depends(get_db),
                   scope: ProjectScope = Depends(require_perm("record:confirm"))):
    """人工确认结论：写 human_verdict + ai_feedbacks；abnormal+order_draft 时创建工单。
    P05（§2.1/2.2/2.3/2.7）：1012 幂等拒绝 + Idempotency-Key 响应缓存 + 行锁串行化 +
    order_no_seq 原子取号 + SLA 配置驱动时限 + 事务提交后通知"""
    user = scope.user

    record = _get_scoped_record(record_id, db, scope)
    if record.inspector_id != user.id: raise forbidden("无权限")
    key = request.headers.get("Idempotency-Key")
    if key is not None and not 8 <= len(key) <= 64: raise BizError(1000, "请求标识长度须为8至64")
    confirm_hash = fingerprint(body.model_dump())
    if record.human_verdict is not None:
        if key and record.confirm_key == key and record.confirm_hash == confirm_hash:
            from .orders import order_to_dict
            prior = db.query(RectifyOrder).filter_by(record_id=record.id).order_by(RectifyOrder.id.desc()).first()
            return ok({"record": record_to_dict(record, user.name, detail=True, db=db),
                "order": order_to_dict(prior, db, detail=True) if prior else None,
                "ai_discarded": not is_actionable_ai_result(loads_safe(record.ai_result))})
        raise BizError(1012, "该巡查记录已确认，不可重复提交")

    record_ai_snapshot = record.ai_result
    # 2. AI 质量回流参数
    ai_result = loads_safe(record.ai_result) or {}
    ai_defect = bool(ai_result.get("has_defect"))
    human_defect = body.human_verdict == "abnormal"
    # P0-4 主防线：AI 判别不可采信（低置信度/无效结论）时，工单三段绝不引用 AI 文本
    ai_discarded = not is_actionable_ai_result(ai_result)

    # ---------- 只读阶段：预计算建单全部输入（无任何写操作）。
    # AI 草稿是网络 IO，必须先于一切数据库变更完成（§2.7 禁止事务内网络 IO）----------
    if human_defect and body.order_draft is None:
        raise BizError(1013, "异常确认必须填写整改工单并指定责任人")
    if not human_defect and body.order_draft is not None:
        raise BizError(1013, "正常结论不能附带整改工单")
    create_order = human_defect
    assignee = ai_draft = item = project = None
    problem_location = rectify_requirement = basis = None
    risk_level = severity = None
    sla_hours: int | None = None
    warn_ratio = None
    if create_order:
        draft = body.order_draft
        assignee = db.get(User, draft.assignee_id)
        # 责任人必须是当前项目内启用的 rectifier 成员（跨项目指派被拒）
        rectifier_membership = (
            db.query(ProjectMember)
            .filter(
                ProjectMember.user_id == draft.assignee_id,
                ProjectMember.project_id == scope.project_id,
                ProjectMember.role == "rectifier",
                ProjectMember.active.is_(True),
            )
            .first()
        )
        if assignee is None or not assignee.active or rectifier_membership is None:
            raise BizError(1011, "指派的责任人不存在或不是本项目整改责任人")
        item = db.get(InspectionItem, record.item_id)
        project = db.get(Project, scope.project_id)
        problem_location = draft.problem_location
        rectify_requirement = draft.rectify_requirement
        basis = draft.basis
        for label, value in (("问题定位", problem_location), ("整改要求", rectify_requirement), ("规范依据", basis)):
            if not isinstance(value, str) or not value.strip() or len(value) > 2000:
                raise BizError(1013, f"请填写{label}（1至2000字）")
        # SLA 时限解析（D5，总纲 §4.4 优先级）：时限一旦写入工单即固化，改规则不影响存量单
        risk_level = item.risk_level if (item is not None and item.risk_level) else "medium"
        severity = ai_result.get("severity") or None  # 快照自 AI 判定
        sla_hours, sla_rule = resolve_sla_hours(item, risk_level, record.defect_category,
                                                scope.project_id, db)
        if draft.sla_hours is not None and draft.sla_hours != sla_hours:
            raise BizError(1014, "整改时限规则已变化，请重新获取草稿后确认")
        warn_ratio = sla_rule.warn_ratio if sla_rule is not None else None

    # ---------- 变更阶段：一次确认 = 一个事务（record + ai_feedback + order + order_log）。
    # 行锁串行化并发重复确认；uq_order_active_record 唯一索引兜底；order_no 撞号重取（最多 3 次）----------
    order = None
    confirmed = record
    for attempt in range(3):
        try:
            # 行锁：丢弃会话缓存重读最新状态（PostgreSQL FOR UPDATE 串行化；SQLite 单写者模型天然串行）
            db.expire(record)
            locked = db.get(InspectionRecord, record.id, with_for_update=True)
            if locked is None:
                raise BizError(1009, "巡查记录不存在")
            if locked.human_verdict is not None:  # 并发重复确认：后拿锁者在此被拒
                raise BizError(1012, "该巡查记录已确认，不可重复提交")
            if locked.ai_result != record_ai_snapshot:
                raise BizError(1014, "分析结果已更新，请刷新并重新确认")
            values = {"human_verdict": body.human_verdict, "confirm_key": key, "confirm_hash": confirm_hash}
            if locked.ai_result is None:
                values["ai_result"] = json.dumps(dict(FALLBACK_RESULT, defect_desc="巡查员直接人工判定"), ensure_ascii=False)
                values["ai_mode"] = "manual"
            if body.note is not None:
                values["note"] = body.note
            claimed = db.execute(update(InspectionRecord).where(
                InspectionRecord.id == record_id,
                InspectionRecord.human_verdict.is_(None),
                InspectionRecord.ai_result == record_ai_snapshot,
            ).values(**values)).rowcount
            if claimed != 1:
                db.rollback()
                fresh = db.get(InspectionRecord, record_id)
                if fresh.human_verdict is not None:
                    raise BizError(1012, "该记录已确认")
                raise BizError(1014, "分析结果已变化，请刷新后确认")
            db.expire(locked)
            if not ai_discarded:
                db.add(AiFeedback(
                    record_id=locked.id,
                    org_id=scope.org_id,
                    project_id=scope.project_id,
                    ai_defect=ai_defect,
                    human_defect=human_defect,
                    consistent=ai_defect == human_defect,
                ))
            if create_order:
                now = now_utc()
                order = RectifyOrder(
                    order_no=next_order_no(db, project),  # 原子取号（D3）：WO-{项目code}-{YYYYMMDD}-{4位序号}
                    org_id=scope.org_id,
                    project_id=scope.project_id,
                    record_id=locked.id,
                    inspector_id=user.id,
                    assignee_id=assignee.id,
                    problem_location=problem_location,
                    rectify_requirement=rectify_requirement,
                    basis=basis,
                    status="pending",
                    deadline=compute_deadline(now, sla_hours),
                    warn_at=compute_warn_at(now, sla_hours, warn_ratio),
                    sla_hours=sla_hours,
                    risk_level=risk_level,
                    severity=severity,
                    round=1,
                )
                db.add(order)
                db.flush()
                db.add(OrderLog(
                    order_id=order.id,
                    from_status=None,
                    to_status="pending",
                    operator_id=user.id,
                    action="create",
                    remark=f"创建工单并指派给 {assignee.name}",
                ))
                # 通知随主事务落库（总纲 §10：改状态+写日志+写通知同一事务），提交后 dispatch_pending 投递
                notify(db, user_id=assignee.id, template_code="ORDER_CREATED",
                       context={"order_no": order.order_no, "problem_location": (problem_location or "")[:80],
                                "risk_level": risk_level},
                       project_id=scope.project_id, org_id=scope.org_id,
                       biz_type="order", biz_id=order.id)
            complete_task(db, locked)
            db.commit()
            confirmed = locked
            break
        except IntegrityError:
            # 唯一冲突二分：uq_order_active_record（同记录并发建单，先者已成功）→ 1012；
            # order_no 全局唯一（跨组织同项目 code 撞号，极罕见）→ 回滚后重取序号重试
            db.rollback()
            fresh = db.get(InspectionRecord, record_id)
            if fresh is not None and fresh.human_verdict is not None:
                raise BizError(1012, "该巡查记录已确认，不可重复提交")
            if attempt == 2:
                raise
            logger.warning("confirm 唯一约束冲突，第 %d 次重试（record=%s）", attempt + 1, record_id)

    db.refresh(confirmed)
    # 通知投递在事务提交后执行：通道失败只落 status/error，不影响主流程（P06 §三）
    if order is not None:
        dispatch_pending(db)
        logger.info("工单 %s 创建（记录 %s → 责任人 %s，SLA=%sh 风险=%s）",
                    order.order_no, confirmed.id, assignee.id, sla_hours, risk_level)

    from .orders import order_to_dict
    payload = {
        "record": record_to_dict(confirmed, user.name, detail=True, db=db),
        "order": order_to_dict(order, db, detail=True) if order else None,
        # P0-4：AI 判别不可采信标记（工单三段为人工/备注内容，未引用 AI 文本）
        "ai_discarded": ai_discarded,
    }
    return ok(payload)


# ---------- 公共项目信息 ----------

@router.get("/api/project")
def get_current_project(db: Session = Depends(get_db), scope: ProjectScope = Depends(project_scope)):
    """当前项目信息（C 端首页项目信息条）"""
    project = db.get(Project, scope.project_id)
    if project is None:
        raise BizError(1023, "项目不存在")
    return ok({"id": project.id, "name": project.name, "code": project.code,
               "project_type": project.project_type, "phase": project.phase, "status": project.status})


# ---------- 巡查记录 ----------

def record_to_dict(record: InspectionRecord, inspector_name: str | None = None, detail: bool = False,
                   related_order: RectifyOrder | None = None, db: Session | None = None) -> dict:
    """巡查记录序列化：photo 为附件对象（鉴权 URL + 存证信息）；ai_result 解析为对象（detail 返回全部字段）"""
    ai_result = loads_safe(record.ai_result)
    if ai_result and not is_actionable_ai_result(ai_result):
        ai_result = dict(ai_result, has_defect=None, status="unknown")
    photo = None
    if record.primary_attachment_id is not None and db is not None:
        att = db.get(Attachment, record.primary_attachment_id)
        if att is not None and att.deleted_at is None:
            photo = attachment_to_dict(att)
    data = {
        "id": record.id,
        "inspector_id": record.inspector_id,
        "project_id": record.project_id,
        "item_id": record.item_id,
        "item_name": record.item_name,
        "photo": photo,
        "photos": [attachment_to_dict(a) for a in db.query(Attachment).filter_by(
            biz_type="record", biz_id=record.id, deleted_at=None).order_by(Attachment.id).all()] if db else [],
        "location": record.location or {},
        "item_snapshot": record.item_snapshot or {"legacy": True, "name": record.item_name, "basis": record.item_basis},
        "human_verdict": record.human_verdict,
        "ai_has_defect": ai_result.get("has_defect") if ai_result else None,
        "ai_mode": record.ai_mode,
        "note": record.note,
        "created_at": fmt(record.created_at),
    }
    if detail:
        data.update({
            "item_basis": record.item_basis,
            "defect_category": record.defect_category,
            "ai_result": ai_result,
            "inspector_name": inspector_name,
            "related_order": ({
                "id": related_order.id,
                "order_no": related_order.order_no,
                "status": related_order.status,
                "round": related_order.round,
            } if related_order else None),
        })
    return data


@router.post("/api/inspection-records")
def create_record(
    body: RecordCreate,
    db: Session = Depends(get_db),
    scope: ProjectScope = Depends(require_perm("record:create")),
):
    """创建巡查记录：检查项快照写入；照片为先上传的附件（先传后绑）；project/org 取自 scope"""
    user = scope.user
    digest = fingerprint(body.model_dump())
    if body.request_key:
        existing = db.query(InspectionRecord).filter_by(project_id=scope.project_id, inspector_id=user.id,
            request_key=body.request_key).first()
        if existing:
            if existing.request_hash != digest: raise BizError(1014, "请求标识已用于不同内容")
            return ok(record_to_dict(existing, user.name, detail=True, db=db))
    item = db.get(InspectionItem, body.item_id)
    # 检查项须启用且属本组织（或平台模板）
    if item is None or not item.enabled or (item.org_id is not None and item.org_id != scope.org_id):
        raise BizError(1007, "检查项不存在或已停用")
    from ..utils import as_utc
    if (item.effective_from and as_utc(item.effective_from)>now_utc()) or (item.effective_until and as_utc(item.effective_until)<=now_utc()):
        raise BizError(1007, "检查标准不在有效期内")
    # 附件校验：本人上传、未绑定、biz_type=record（P04 §2.5.1）
    att = load_bindable_attachment(db, body.attachment_id, scope, "record")
    extra = [load_bindable_attachment(db, aid, scope, "record") for aid in set(body.attachment_ids)-{att.id}]
    task = None
    if body.task_id is not None:
        from ..p1_models import PlanTask, InspectionPlan
        task = db.get(PlanTask, body.task_id, with_for_update=True)
        if not task or task.project_id != scope.project_id or task.inspector_id != user.id or task.record_id is not None or task.status not in ("pending", "missed") or db.get(InspectionPlan, task.plan_id).item_id != item.id:
            raise BizError(1014, "计划任务不可绑定，请刷新任务列表")
    record = InspectionRecord(
        item_snapshot=freeze_item(db, item), location=body.location.model_dump(),
        request_key=body.request_key, request_hash=digest,
        org_id=scope.org_id,
        inspector_id=user.id,
        project_id=scope.project_id,
        item_id=item.id,
        item_name=item.name,       # 快照冗余
        item_basis=item.basis,     # 快照冗余
        defect_category=item.defect_category,  # 快照冗余
        primary_attachment_id=att.id,
        note=body.note,
        human_verdict=None,  # 待确认
    )
    db.add(record)
    try:
        db.flush()
    except (IntegrityError, OperationalError) as exc:
        db.rollback()
        # MySQL may deadlock when two requests claim the same unique idempotency key.
        # The committed winner is returned on the caller's retry; other DB errors must surface.
        mysql_retryable = (db.bind is not None and db.bind.dialect.name in ("mysql", "mariadb")
                           and getattr(exc.orig, "args", [None])[0] in (1205, 1213))
        if not isinstance(exc, IntegrityError) and not mysql_retryable:
            raise
        existing = db.query(InspectionRecord).filter_by(project_id=scope.project_id, inspector_id=user.id,
            request_key=body.request_key).first() if body.request_key else None
        if existing and existing.request_hash == digest:
            return ok(record_to_dict(existing, user.name, detail=True, db=db))
        raise BizError(1014, "记录或检查标准已被并发更新，请重试")
    bind_attachment(att, record.id)
    for photo in extra: bind_attachment(photo, record.id)
    if task:
        if db.execute(update(PlanTask).where(PlanTask.id==task.id, PlanTask.record_id.is_(None))
            .values(record_id=record.id)).rowcount != 1:
            raise BizError(1014, "任务已被绑定，请刷新")
    db.commit()
    db.refresh(record)
    return ok(record_to_dict(record, user.name, detail=True, db=db))


@router.get("/api/inspection-records")
def list_records(
    verdict: str | None = None,
    inspector_id: int | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    scope: ProjectScope = Depends(project_scope),
):
    """巡查记录列表：强制 project 过滤；inspector 仅本人，可读全部的角色看本项目全部"""
    q = db.query(InspectionRecord).filter(InspectionRecord.project_id == scope.project_id)
    if scope.can_read_all:
        if inspector_id is not None:
            q = q.filter(InspectionRecord.inspector_id == inspector_id)
    elif "inspector" in scope.effective_roles:
        q = q.filter(InspectionRecord.inspector_id == scope.user.id)
    else:
        raise forbidden("无权限")
    if verdict in ("normal", "abnormal"):
        q = q.filter(InspectionRecord.human_verdict == verdict)
    elif verdict == "none":
        q = q.filter(InspectionRecord.human_verdict.is_(None))
    total = q.count()
    records = q.order_by(InspectionRecord.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return ok({"list": [record_to_dict(r, db=db) for r in records], "total": total, "page": page, "page_size": page_size})


@router.get("/api/inspection-records/{record_id}")
def record_detail(
    record_id: int,
    db: Session = Depends(get_db),
    scope: ProjectScope = Depends(project_scope),
):
    """巡查记录详情：须属本项目；inspector 仅本人，可读全部的角色任意；含关联工单摘要"""
    record = _get_scoped_record(record_id, db, scope)
    if not scope.can_read_all and record.inspector_id != scope.user.id:
        raise forbidden("无权限")
    inspector = db.get(User, record.inspector_id)
    related_order = (
        db.query(RectifyOrder)
        .filter(RectifyOrder.record_id == record.id, RectifyOrder.project_id == scope.project_id)
        .order_by(RectifyOrder.id.desc()).first()
    )
    return ok(record_to_dict(record, inspector.name if inspector else None, detail=True,
                             related_order=related_order, db=db))
