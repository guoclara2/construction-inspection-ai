# 报表导出路由：巡查报告 / 整改报告 / 项目汇总报告（PDF，可盖章留痕）
# 权限统一走 require_perm("export")（project_admin / org_admin），生成动作写审计日志。
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
from json import dumps as _json_dumps
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import ProjectScope, require_perm
from ..models import (
    Attachment,
    InspectionRecord,
    OrderLog,
    Organization,
    Project,
    RectifyOrder,
    User,
)
from ..services import reporting as R
from ..services.audit import write_audit
from ..services.operations import feedback_history
from ..services.storage import get_storage
from ..utils import BizError, as_utc, fmt, forbidden, loads_safe, now_utc

router = APIRouter(prefix="/api/reports", tags=["reports"])

# 现场位置字段中文映射（与 schemas.SiteLocation 保持一致）
_LOC = {
    "building": "楼栋", "floor": "楼层", "axis": "轴线", "chainage": "桩号",
    "equipment": "设备", "measurement": "量测", "annotation": "备注", "custom": "自定义",
}
_AI_MODE = {"real": "真实模型", "mock": "演示模型", "manual": "人工判定", None: "—"}


def _pdf_response(pdf: bytes, filename: str) -> Response:
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"report.pdf\"; filename*=UTF-8''{quote(filename)}"
            ),
        },
    )


def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def _fingerprint(payload: dict) -> str:
    return sha256(_json_dumps(payload, ensure_ascii=False, sort_keys=True,
                              default=str).encode("utf-8")).hexdigest()[:16]


def _org_project(db: Session, scope: ProjectScope) -> tuple[str, str, str]:
    """返回 (org_name, project_name, project_code)。"""
    org = db.get(Organization, scope.org_id)
    project = db.get(Project, scope.project_id)
    org_name = org.name if org else ""
    project_name = project.name if project else ""
    code = (project.code if project and project.code else str(scope.project_id))
    return org_name, project_name, code


def _base_meta(db, scope, *, project_name, project_code, org_name, title, report_no, fingerprint) -> dict:
    return {
        "org_name": org_name,
        "project_name": project_name,
        "report_title": title,
        "report_no": report_no,
        "generated_by": scope.user.name,
        "generated_at": fmt(now_utc()) or "",
        "fingerprint": fingerprint,
    }


def _location_str(loc: dict | None) -> str:
    if not loc:
        return "—"
    parts = [f"{label}: {loc[key]}" for key, label in _LOC.items() if loc.get(key)]
    return "；".join(parts) or "—"


def _has_defect_label(ai: dict) -> str:
    v = ai.get("has_defect")
    if v is True:
        return "存在缺陷"
    if v is False:
        return "未见缺陷"
    return "无法判断"


# ---------- 巡查报告 ----------

@router.get("/record/{record_id}")
def record_report(record_id: int, request: Request, db: Session = Depends(get_db),
                  scope: ProjectScope = Depends(require_perm("export"))):
    """巡查报告：单条巡查记录的正式 PDF（信息 + 检查要点 + AI 分析 + 现场照片 + 关联工单）。"""
    record = db.get(InspectionRecord, record_id)
    if record is None or record.project_id != scope.project_id:
        raise BizError(1009, "巡查记录不存在")

    org_name, project_name, project_code = _org_project(db, scope)
    inspector = db.get(User, record.inspector_id)
    snapshot = record.item_snapshot or {}
    cp_raw = snapshot.get("check_points")
    check_points = loads_safe(cp_raw) if isinstance(cp_raw, str) else (cp_raw or [])
    ai = loads_safe(record.ai_result) or {}
    order = (
        db.query(RectifyOrder)
        .filter(RectifyOrder.record_id == record.id, RectifyOrder.project_id == scope.project_id)
        .order_by(RectifyOrder.id.desc()).first()
    )
    photos = (
        db.query(Attachment)
        .filter(Attachment.biz_type == "record", Attachment.biz_id == record.id,
                Attachment.deleted_at.is_(None))
        .order_by(Attachment.sort_no, Attachment.id).all()
    )

    report_no = f"RPT-{project_code}-REC-{record.id}"
    fingerprint = _fingerprint({
        "type": "record", "record_id": record.id, "item_name": record.item_name,
        "verdict": record.human_verdict, "created_at": fmt(record.created_at),
        "inspector_id": record.inspector_id,
        "order_no": order.order_no if order else None,
    })
    meta = _base_meta(db, scope, project_name=project_name, project_code=project_code,
                      org_name=org_name, title="工程现场巡查报告", report_no=report_no,
                      fingerprint=fingerprint)

    story = R.title_block(meta)
    story += R.heading("基本信息")
    story.append(R.info_table([
        ("项目名称", project_name),
        ("项目编号", project_code or "—"),
        ("巡查员", inspector.name if inspector else "—"),
        ("检查项", record.item_name),
        ("缺陷类别", R.CATEGORY_LABEL.get(record.defect_category, record.defect_category)),
        ("风险等级", R.RISK_LABEL.get(snapshot.get("risk_level"), snapshot.get("risk_level") or "—")),
        ("判定结论", R.VERDICT_LABEL.get(record.human_verdict, "待确认")),
        ("巡查时间", fmt(record.created_at) or "—"),
    ]))
    story.append(R.kv_para("巡查地点", _location_str(record.location)))
    story.append(R.kv_para("检查依据", record.item_basis))
    if record.note:
        story.append(R.kv_para("巡查备注", record.note))

    story += R.heading("检查要点")
    if check_points:
        story += R.bullets([str(c) for c in check_points])
    else:
        story.append(R.kv_para("检查要点", "—"))

    story += R.heading("智能分析结果")
    story.append(R.info_table([
        ("分析模式", _AI_MODE.get(record.ai_mode, record.ai_mode)),
        ("缺陷判定", _has_defect_label(ai)),
        ("严重程度", _display(ai.get("severity"))),
        ("置信度", f"{int(round(float(ai.get('confidence') or 0) * 100))}%"),
    ]))
    story.append(R.kv_para("缺陷描述", ai.get("defect_desc")))
    story.append(R.kv_para("判定依据", ai.get("basis")))
    story.append(R.kv_para("处置建议", ai.get("suggestion")))

    story += R.heading("现场照片")
    story.append(R.image_grid(get_storage(), photos))

    if order is not None:
        assignee = db.get(User, order.assignee_id)
        story += R.heading("关联整改工单")
        story.append(R.info_table([
            ("工单号", order.order_no),
            ("状态", R.STATUS_LABEL.get(order.status, order.status)),
            ("整改责任人", assignee.name if assignee else "—"),
            ("整改期限", fmt(order.deadline) or "—"),
        ]))

    story += R.heading("存证与签章")
    story.append(R.evidence_block(meta))

    write_audit(db, "report_export", actor_id=scope.user.id, target_type="record",
                target_id=record.id, result="success", org_id=scope.org_id,
                project_id=scope.project_id, after={"report_no": report_no},
                ip=_client_ip(request), user_agent=request.headers.get("user-agent"))
    return _pdf_response(R.render_report(meta, story), f"{report_no}.pdf")


def _display(value) -> str:
    return str(value) if value not in (None, "") else "—"


# ---------- 整改报告 ----------

@router.get("/order/{order_id}")
def order_report(order_id: int, request: Request, db: Session = Depends(get_db),
                 scope: ProjectScope = Depends(require_perm("export"))):
    """整改报告：单张整改工单的正式 PDF（问题/要求/依据 + 流转日志 + 整改反馈 + 照片）。"""
    order = db.get(RectifyOrder, order_id)
    if order is None or order.project_id != scope.project_id:
        raise BizError(1020, "工单不存在")

    org_name, project_name, project_code = _org_project(db, scope)
    record = db.get(InspectionRecord, order.record_id)
    inspector = db.get(User, order.inspector_id)
    assignee = db.get(User, order.assignee_id)
    record_photos = []
    if record is not None:
        record_photos = (
            db.query(Attachment)
            .filter(Attachment.biz_type == "record", Attachment.biz_id == record.id,
                    Attachment.deleted_at.is_(None))
            .order_by(Attachment.sort_no, Attachment.id).all()
        )
    feedback_photos = (
        db.query(Attachment)
        .filter(Attachment.biz_type == "order_feedback", Attachment.biz_id == order.id,
                Attachment.deleted_at.is_(None))
        .order_by(Attachment.round, Attachment.sort_no, Attachment.id).all()
    )
    logs = db.query(OrderLog).filter(OrderLog.order_id == order.id).order_by(OrderLog.id.asc()).all()
    rounds = feedback_history(db, order)

    overdue = order.status not in ("closed", "cancelled") and as_utc(order.deadline) < now_utc()
    status_disp = R.STATUS_LABEL.get(order.status, order.status) + ("（已超期）" if overdue else "")

    report_no = f"RPT-{project_code}-RET-{order.id}"
    fingerprint = _fingerprint({
        "type": "order", "order_id": order.id, "order_no": order.order_no,
        "status": order.status, "deadline": fmt(order.deadline),
        "review_result": order.review_result, "round": order.round,
    })
    meta = _base_meta(db, scope, project_name=project_name, project_code=project_code,
                      org_name=org_name, title="工程整改报告", report_no=report_no,
                      fingerprint=fingerprint)

    story = R.title_block(meta)
    story += R.heading("工单基本信息")
    story.append(R.info_table([
        ("工单号", order.order_no),
        ("项目名称", project_name),
        ("巡查员", inspector.name if inspector else "—"),
        ("整改责任人", assignee.name if assignee else "—"),
        ("当前状态", status_disp),
        ("风险等级", R.RISK_LABEL.get(order.risk_level, order.risk_level or "—")),
        ("下发时间", fmt(order.created_at) or "—"),
        ("整改期限", fmt(order.deadline) or "—"),
    ]))
    if order.sla_hours is not None:
        story.append(R.kv_para("整改时限（SLA）", f"{order.sla_hours} 小时"))
    if order.closed_at:
        story.append(R.kv_para("闭环时间", fmt(order.closed_at)))

    story += R.heading("问题与要求")
    story.append(R.kv_para("问题定位", order.problem_location))
    story.append(R.kv_para("整改要求", order.rectify_requirement))
    story.append(R.kv_para("规范依据", order.basis))

    story += R.heading("整改进程")
    if logs:
        rows = []
        for lg in logs:
            op = db.get(User, lg.operator_id)
            flow = f"{R.STATUS_LABEL.get(lg.from_status, lg.from_status or '—')} → {R.STATUS_LABEL.get(lg.to_status, lg.to_status)}"
            rows.append([
                fmt(lg.created_at) or "—",
                R.ACTION_LABEL.get(lg.action, lg.action),
                op.name if op else ("系统" if lg.operator_id is None else "—"),
                flow,
                _display(lg.remark),
            ])
        story.append(R.data_table(
            ["时间", "操作", "操作人", "状态流转", "备注"], rows,
            [30 * R.mm, 22 * R.mm, 22 * R.mm, 34 * R.mm, 66 * R.mm],
        ))
    else:
        story.append(R.kv_para("整改进程", "暂无流转记录"))

    if rounds:
        story += R.heading("整改反馈记录")
        for r in rounds:
            submitter = db.get(User, r["submitter_id"]) if r.get("submitter_id") else None
            result = r.get("result")
            result_disp = {"pass": "审核通过", "reject": "审核打回"}.get(result, "—")
            lines = [
                f"第 {r['round']} 轮：{_display(r.get('text'))}",
                f"提交人：{submitter.name if submitter else '—'}　提交时间：{_display(r.get('submitted_at'))}",
            ]
            if result:
                lines.append(f"审核结论：{result_disp}　审核意见：{_display(r.get('comment'))}")
            story.append(R.kv_para(f"反馈[{r['round']}]", "；".join(lines)))
    else:
        story += R.heading("整改反馈记录")
        story.append(R.kv_para("整改反馈", "暂无反馈"))

    if feedback_photos:
        story += R.heading("整改反馈照片")
        story.append(R.image_grid(get_storage(), feedback_photos))

    if record is not None:
        story += R.heading("关联巡查记录")
        story.append(R.info_table([
            ("检查项", record.item_name),
            ("判定结论", R.VERDICT_LABEL.get(record.human_verdict, "待确认")),
            ("巡查时间", fmt(record.created_at) or "—"),
            ("巡查员", inspector.name if inspector else "—"),
        ]))
        story.append(R.kv_para("检查依据", record.item_basis))
        if record_photos:
            story.append(R.image_grid(get_storage(), record_photos))

    story += R.heading("存证与签章")
    story.append(R.evidence_block(meta))

    write_audit(db, "report_export", actor_id=scope.user.id, target_type="order",
                target_id=order.id, result="success", org_id=scope.org_id,
                project_id=scope.project_id, after={"report_no": report_no},
                ip=_client_ip(request), user_agent=request.headers.get("user-agent"))
    return _pdf_response(R.render_report(meta, story), f"{report_no}.pdf")


# ---------- 项目汇总报告 ----------

@router.get("/summary")
def summary_report(
    request: Request,
    start: date = Query(...),
    end: date = Query(...),
    db: Session = Depends(get_db),
    scope: ProjectScope = Depends(require_perm("export")),
):
    """项目汇总报告：指定日期范围内的巡查与整改统计分析（集团汇报 / 竣工验收）。"""
    if end < start:
        raise BizError(1000, "结束日期不能早于开始日期")

    org_name, project_name, project_code = _org_project(db, scope)
    tz = ZoneInfo(settings.APP_TIMEZONE)
    start_utc = datetime.combine(start, time.min, tz).astimezone(timezone.utc)
    end_utc = (datetime.combine(end, time.min, tz) + timedelta(days=1)).astimezone(timezone.utc)

    records = (
        db.query(InspectionRecord)
        .filter(InspectionRecord.project_id == scope.project_id,
                InspectionRecord.created_at >= start_utc,
                InspectionRecord.created_at < end_utc)
        .order_by(InspectionRecord.created_at.desc()).all()
    )
    orders = (
        db.query(RectifyOrder)
        .filter(RectifyOrder.project_id == scope.project_id,
                RectifyOrder.created_at >= start_utc,
                RectifyOrder.created_at < end_utc)
        .order_by(RectifyOrder.created_at.desc()).all()
    )

    total_records = len(records)
    abnormal = sum(1 for r in records if r.human_verdict == "abnormal")
    normal = sum(1 for r in records if r.human_verdict == "normal")
    unconfirmed = total_records - abnormal - normal

    order_total = len(orders)
    by_status = {k: 0 for k in R.STATUS_LABEL}
    for o in orders:
        by_status[o.status] = by_status.get(o.status, 0) + 1
    closed = by_status.get("closed", 0)
    overdue = sum(1 for o in orders
                  if o.status not in ("closed", "cancelled") and as_utc(o.deadline) < now_utc())
    closed_rate = round(closed * 100.0 / order_total, 1) if order_total else 0.0

    category_dist = {}
    for r in records:
        c = R.CATEGORY_LABEL.get(r.defect_category, r.defect_category or "未分类")
        category_dist[c] = category_dist.get(c, 0) + 1
    risk_dist = {}
    for o in orders:
        rk = R.RISK_LABEL.get(o.risk_level, o.risk_level or "未分级")
        risk_dist[rk] = risk_dist.get(rk, 0) + 1

    # 明细表：记录 + 关联工单
    order_by_record: dict[int, RectifyOrder] = {}
    for o in orders:
        order_by_record.setdefault(o.record_id, o)
    user_name: dict[int, str] = {}
    for uid in {r.inspector_id for r in records}:
        u = db.get(User, uid)
        if u:
            user_name[uid] = u.name

    detail_rows = []
    for r in records:
        o = order_by_record.get(r.id)
        detail_rows.append([
            fmt(r.created_at) or "—",
            r.item_name,
            user_name.get(r.inspector_id, "—"),
            R.VERDICT_LABEL.get(r.human_verdict, "待确认"),
            o.order_no if o else "—",
            R.STATUS_LABEL.get(o.status, "—") if o else "—",
        ])

    report_no = f"RPT-{project_code}-SUM-{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}"
    fingerprint = _fingerprint({
        "type": "summary", "start": str(start), "end": str(end),
        "total_records": total_records, "abnormal": abnormal, "normal": normal,
        "order_total": order_total, "closed": closed, "overdue": overdue,
    })
    meta = _base_meta(db, scope, project_name=project_name, project_code=project_code,
                      org_name=org_name, title="项目巡查汇总报告", report_no=report_no,
                      fingerprint=fingerprint)

    story = R.title_block(meta)
    story += R.heading("统计概览")
    story.append(R.info_table([
        ("统计周期", f"{start} 至 {end}"),
        ("项目名称", project_name),
        ("巡查记录总数", total_records),
        ("正常记录", normal),
        ("异常记录", abnormal),
        ("待确认记录", unconfirmed),
        ("整改工单总数", order_total),
        ("已闭环工单", closed),
    ]))
    story.append(R.kv_para("闭环率", f"{closed_rate}%"))
    story.append(R.kv_para("超期未闭环", overdue))

    story += R.heading("工单状态分布")
    status_rows = [[k, by_status.get(k, 0)] for k in R.STATUS_LABEL]
    story.append(R.data_table(["状态", "数量"], status_rows, [87 * R.mm, 87 * R.mm]))

    story += R.heading("缺陷类别分布")
    cat_rows = [[k, v] for k, v in sorted(category_dist.items(), key=lambda x: -x[1])] or [["—", "—"]]
    story.append(R.data_table(["缺陷类别", "数量"], cat_rows, [87 * R.mm, 87 * R.mm]))
    story += R.heading("风险等级分布")
    risk_rows = [[k, v] for k, v in sorted(risk_dist.items(), key=lambda x: -x[1])] or [["—", "—"]]
    story.append(R.data_table(["风险等级", "数量"], risk_rows, [87 * R.mm, 87 * R.mm]))

    story += R.heading("巡查明细")
    story.append(R.data_table(
        ["时间", "检查项", "巡查员", "结论", "关联工单", "工单状态"],
        detail_rows,
        [32 * R.mm, 46 * R.mm, 22 * R.mm, 18 * R.mm, 30 * R.mm, 26 * R.mm],
    ))

    story += R.heading("存证与签章")
    story.append(R.evidence_block(meta))

    write_audit(db, "report_export", actor_id=scope.user.id, target_type="project",
                target_id=scope.project_id, result="success", org_id=scope.org_id,
                project_id=scope.project_id, after={"report_no": report_no, "start": str(start), "end": str(end)},
                ip=_client_ip(request), user_agent=request.headers.get("user-agent"))
    return _pdf_response(R.render_report(meta, story), f"{report_no}.pdf")