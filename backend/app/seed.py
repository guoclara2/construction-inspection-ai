# 种子数据：组织 + 2 项目 + 成员 + 知识库 + SLA 规则（基础）；两项目各自演示业务数据（均幂等，仅非 prod）
import hashlib
import json
import logging
import os
import uuid
from datetime import timedelta

from sqlalchemy.orm import Session

from .config import DATA_DIR, settings
from .models import (
    AiFeedback,
    Attachment,
    InspectionItem,
    InspectionRecord,
    NotificationTemplate,
    OrderLog,
    Organization,
    Project,
    ProjectMember,
    RectifyOrder,
    SlaRule,
    User,
)
from .security import generate_strong_password, hash_password
from .services.sla import compute_warn_at
from .services.storage import get_storage
from .utils import now_utc, to_local

logger = logging.getLogger(__name__)

# 检查项种子 24 条：name, check_points, basis, defect_category, risk_level, types, phases
SEED_ITEMS = [
    ("基坑支护结构完整性检查",
     ["支护构件有无裂缝、变形、位移", "锚杆、土钉有无拔出或失效迹象", "冠梁、腰梁连接节点是否牢固"],
     "JGJ 59-2011《建筑施工安全检查标准》表3.13", "structural", "high",
     ["building", "tunnel"], ["foundation"]),
    ("基坑临边防护检查",
     ["临边是否设置连续封闭防护栏杆", "栏杆高度、立柱间距是否符合规范", "夜间是否设置警示标识"],
     "JGJ 59-2011《建筑施工安全检查标准》表3.13", "protection", "high",
     ["building", "road", "tunnel", "landscape"], ["foundation"]),
    ("基坑降水与排水检查",
     ["降水设备运行是否正常", "坑内有无明显积水", "排水沟、集水井是否通畅"],
     "JGJ 59-2011《建筑施工安全检查标准》表3.13", "safety", "medium",
     ["building", "tunnel"], ["foundation"]),
    ("基坑坑边堆载检查",
     ["坑边堆载是否超过设计允许值", "堆载距坑边距离是否符合方案要求"],
     "JGJ 120《建筑基坑支护技术规程》", "structural", "high",
     ["building", "tunnel"], ["foundation"]),
    ("模板支撑体系检查",
     ["立杆间距、扫地杆、剪刀撑设置是否符合方案", "支撑体系有无明显变形", "可调托座伸出长度是否超限"],
     "JGJ 162《建筑施工模板安全技术规范》", "structural", "high",
     ["building"], ["structure"]),
    ("钢筋安装质量检查",
     ["钢筋规格、数量、间距是否符合设计", "保护层厚度是否达标", "接头位置及质量是否符合规范"],
     "GB 50204《混凝土结构工程施工质量验收规范》", "material", "medium",
     ["building"], ["structure"]),
    ("混凝土外观质量检查",
     ["有无露筋、蜂窝、麻面、裂缝", "构件截面尺寸偏差是否超限"],
     "GB 50204《混凝土结构工程施工质量验收规范》", "material", "medium",
     ["building"], ["structure"]),
    ("脚手架搭设规范性检查",
     ["立杆基础是否坚实、垫板符合要求", "连墙件设置是否到位", "剪刀撑、脚手板铺设是否规范"],
     "JGJ 130《建筑施工扣件式钢管脚手架安全技术规范》", "safety", "high",
     ["building", "landscape"], ["structure", "decoration"]),
    ("高处作业安全防护检查",
     ["作业人员安全带佩戴情况", "临边洞口防护设施是否完好", "安全网张挂是否规范"],
     "JGJ 80《建筑施工高处作业安全技术规范》", "protection", "high",
     ["building", "road", "tunnel"], ["structure"]),
    ("施工现场临时用电检查",
     ["配电箱\"三级配电两级保护\"落实情况", "漏电保护器参数是否匹配", "线路敷设是否规范、有无私拉乱接"],
     "JGJ 46《施工现场临时用电安全技术规范》", "safety", "high",
     ["building", "road", "tunnel", "landscape"], ["structure", "mep"]),
    ("施工现场消防设施检查",
     ["灭火器配置数量与压力是否正常", "动火作业审批与监护是否落实", "消防通道是否畅通"],
     "GB 50720《建设工程施工现场消防安全技术规范》", "safety", "high",
     ["building", "road", "tunnel", "landscape"], ["foundation", "structure", "mep", "decoration"]),
    ("建筑材料堆放与标识检查",
     ["材料堆放是否整齐、分类清晰", "标识牌（名称/规格/检验状态）是否齐全"],
     "JGJ 59-2011《建筑施工安全检查标准》", "material", "low",
     ["building", "road", "tunnel", "landscape"], ["foundation", "structure", "mep", "decoration"]),
    ("机电管线安装规范性检查",
     ["管线走向、标高、坡度是否符合设计", "支吊架间距与固定是否规范", "管线穿越防火分区封堵是否到位"],
     "GB 50242《建筑给水排水及采暖工程施工质量验收规范》", "installation", "medium",
     ["building", "road", "tunnel"], ["mep"]),
    ("机电设备接地检查",
     ["设备金属外壳接地是否可靠", "接地电阻是否符合规范要求", "接地线规格是否正确"],
     "GB 50169《电气装置安装工程接地装置施工及验收规范》", "installation", "high",
     ["building"], ["mep"]),
    ("装修墙面平整度检查",
     ["墙面平整度、垂直度偏差是否超限", "阴阳角是否方正", "有无空鼓开裂"],
     "GB 50210《建筑装饰装修工程质量验收标准》", "material", "low",
     ["building"], ["decoration"]),
    ("门窗安装质量检查",
     ["门窗框固定是否牢固", "边缝填嵌是否饱满", "开启是否灵活、密封是否良好"],
     "GB 50210《建筑装饰装修工程质量验收标准》", "installation", "low",
     ["building"], ["decoration"]),
    ("道路路基压实度检查",
     ["压实度检测是否合格", "路基有无翻浆、弹簧现象", "分层填筑厚度是否超限"],
     "JTG F10《公路路基施工技术规范》", "material", "high",
     ["road"], ["foundation"]),
    ("道路面层施工质量检查",
     ["面层厚度、平整度是否符合设计", "接缝处理是否规范", "有无裂缝、松散"],
     "JTG F30《公路水泥混凝土路面施工技术细则》", "material", "medium",
     ["road"], ["structure"]),
    ("隧道衬砌渗漏水检查",
     ["衬砌表面有无渗漏点", "施工缝、变形缝防水是否完好", "排水系统是否通畅"],
     "JTG/T F60《公路隧道施工技术细则》", "structural", "high",
     ["tunnel"], ["structure"]),
    ("隧道通风与照明检查",
     ["通风设备运行是否正常", "洞内照明照度是否达标", "应急照明是否完好"],
     "JTG/T D71《公路隧道交通工程设计规范》", "installation", "medium",
     ["tunnel"], ["mep"]),
    ("景观苗木支撑与养护检查",
     ["新栽苗木支撑是否牢固", "浇水养护是否到位", "有无倒伏、枯死"],
     "CJJ 82《园林绿化工程施工及验收规范》", "material", "low",
     ["landscape"], ["decoration"]),
    ("景观给排水管道安装检查",
     ["管道埋深是否符合设计", "接口严密无渗漏", "阀门井砌筑是否规范"],
     "GB 50242《建筑给水排水及采暖工程施工质量验收规范》", "installation", "medium",
     ["landscape"], ["mep"]),
    ("施工机械设备运行状况检查",
     ["特种设备检验合格证是否在有效期", "安全防护装置是否完好", "维保记录是否齐全"],
     "JGJ 33《建筑机械使用安全技术规程》", "safety", "medium",
     ["building", "road", "tunnel", "landscape"], ["foundation", "structure", "mep", "decoration"]),
    ("安全警示标识设置检查",
     ["危险部位警示标识是否齐全醒目", "夜间警示灯是否正常", "标识内容是否准确"],
     "JGJ 59-2011《建筑施工安全检查标准》", "protection", "medium",
     ["building", "road", "tunnel", "landscape"], ["foundation", "structure", "mep", "decoration"]),
]

# 用户种子：username, name, phone, is_org_admin
SEED_USERS = [
    ("admin", "张建国", "13800000001", True),
    ("liming", "李明", "13800000002", False),
    ("wangfang", "王芳", "13800000003", False),
    ("zhaoqiang", "赵强", "13800000004", False),
    ("sunwei", "孙伟", "13800000005", False),
    ("zhoutao", "周涛", "13800000006", False),
    ("chenjing", "陈静", "13800000007", False),
    ("zhaomin", "赵敏", "13800000008", False),
    ("linwei", "林伟", "13800000009", False),
]

# 成员关系：username, project_code, role, specialty, org_unit, is_default
# 用于验证隔离：liming 仅 P1；chenjing/zhaomin 仅 P2；wangfang 跨 P1+P2；admin 组织级横跨两项目
SEED_MEMBERS = [
    ("admin", "XCDS", "project_admin", "safety", "示范建设集团", True),
    ("admin", "BJLD", "project_admin", "safety", "示范建设集团", False),
    ("liming", "XCDS", "inspector", "safety", "监理单位", True),
    ("wangfang", "XCDS", "inspector", "civil", "总承包单位", True),
    ("wangfang", "BJLD", "inspector", "civil", "总承包单位", False),
    ("zhaoqiang", "XCDS", "rectifier", "civil", "土建分包", True),
    ("sunwei", "XCDS", "rectifier", "mech", "机电分包", True),
    ("zhoutao", "XCDS", "rectifier", "deco", "装修分包", True),
    ("linwei", "XCDS", "reviewer", "safety", "监理单位", True),
    ("chenjing", "BJLD", "inspector", "safety", "监理单位", True),
    ("zhaomin", "BJLD", "rectifier", "civil", "道路分包", True),
]


# 通知模板种子（总纲 §7 全量 14 个）：code, channel, title_tpl, content_tpl
# 通道集合按 P06 §2.1.4 策略表；wx_template_id / sms_template_code 留空由管理端按实际申请的模板 ID 配置
SEED_NOTIFICATION_TEMPLATES = [
    ("ORDER_CREATED", "inapp,wechat",
     "新整改工单 {order_no}",
     "您有一张新的整改工单待接收：{order_no}，问题：{problem_location}"),
    ("ORDER_ACCEPTED", "inapp,wechat",
     "工单 {order_no} 已接收",
     "责任人已接收工单 {order_no}，即将开始整改"),
    ("ORDER_STARTED", "inapp,wechat",
     "工单 {order_no} 开始整改",
     "责任人已开始整改工单 {order_no}"),
    ("ORDER_FEEDBACK", "inapp,wechat",
     "工单 {order_no} 待您审核",
     "责任人已提交整改反馈，请及时审核工单 {order_no}"),
    ("ORDER_REVIEW_PASS", "inapp,wechat",
     "工单 {order_no} 已闭环",
     "巡查员审核通过，工单 {order_no} 已闭环"),
    ("ORDER_REVIEW_REJECT", "inapp,wechat",
     "工单 {order_no} 被打回（第 {round} 轮）",
     "巡查员审核不通过，工单 {order_no} 已打回，请继续整改并重新提交反馈"),
    ("ORDER_URGE", "inapp,wechat",
     "工单 {order_no} 催办提醒",
     "管理员催办：请尽快处理整改工单 {order_no}"),
    ("ORDER_WARN", "inapp,wechat",
     "工单 {order_no} 临期提醒",
     "工单 {order_no} 距整改截止仅剩 {remain_hours} 小时，请尽快完成整改"),
    ("ORDER_OVERDUE", "inapp,wechat,sms",
     "工单 {order_no} 已超期",
     "工单 {order_no} 已超期 {overdue_hours} 小时，请立即处理并反馈"),
    ("ORDER_ESCALATE", "inapp,wechat,sms",
     "工单 {order_no} 超期升级",
     "工单 {order_no} 已超期 {overdue_hours} 小时（责任人 {assignee_name}），已通知{role_name}督办"),
    ("ORDER_TRANSFER", "inapp,wechat",
     "工单 {order_no} 已改派",
     "工单 {order_no} 已改派给 {new_assignee_name}，原因：{reason}"),
    ("ORDER_CANCELLED", "inapp,wechat",
     "工单 {order_no} 已作废",
     "工单 {order_no} 已作废，原因：{reason}"),
    ("ORDER_EXTEND_RESULT", "inapp,wechat",
     "工单 {order_no} 延期审批结果",
     "您为工单 {order_no} 申请的延期已{result}：{comment}"),
    ("PLAN_TASK_DUE", "inapp,wechat",
     "巡查任务到期提醒",
     "巡查任务「{plan_name}」将于 {deadline} 截止，请及时执行"),
    ("PLAN_TASK_MISSED", "inapp,wechat",
     "巡查任务漏检通知",
     "巡查任务「{plan_name}」已超期未执行，已标记为漏检"),
]


def seed_notification_templates(db: Session):
    """通知模板种子（幂等，全环境含 prod）：按 code 缺失才插入，不覆盖已配置的模板 ID。
    wx_template_id 初始取环境变量（订单族 ← WX_SUBSCRIBE_TEMPLATE_ORDER，超期族 ← WX_SUBSCRIBE_TEMPLATE_OVERDUE），
    未配置留空 → wechat 通道 skipped（明确降级，不阻断业务）。"""
    existing = {row[0] for row in db.query(NotificationTemplate.code).all()}
    order_tpl = settings.WX_SUBSCRIBE_TEMPLATE_ORDER.strip()
    overdue_tpl = settings.WX_SUBSCRIBE_TEMPLATE_OVERDUE.strip()
    created = 0
    for code, channel, title_tpl, content_tpl in SEED_NOTIFICATION_TEMPLATES:
        if code in existing:
            continue
        wx_template_id = overdue_tpl if code in ("ORDER_WARN", "ORDER_OVERDUE", "ORDER_ESCALATE") else order_tpl
        db.add(NotificationTemplate(
            code=code, channel=channel, title_tpl=title_tpl,
            content_tpl=content_tpl, wx_template_id=wx_template_id or None,
            sms_template_code=None, enabled=True,
        ))
        created += 1
    if created:
        db.commit()
        logger.info("通知模板种子已写入 %d 个（总 %d）", created, len(SEED_NOTIFICATION_TEMPLATES))


def _write_seed_credentials_file(password: str) -> str:
    """随机种子密码落盘 data/seed_credentials.txt（P0-1：启动日志窗口关闭即丢失的问题）。
    本文件为演示环境一次性凭据，生产部署前必须删除；生产应由 DEV_SEED_PASSWORD 显式指定密码。
    注：Windows 下 icacls 权限收敛易受环境差异影响，此处不做权限设置，交由部署侧保证。"""
    path = os.path.join(DATA_DIR, "seed_credentials.txt")
    os.makedirs(DATA_DIR, exist_ok=True)
    content = (
        "# 演示环境一次性凭据（DEV_SEED_PASSWORD 未设置时自动生成）\n"
        "# 注意：生产部署前必须删除本文件，并通过环境变量 DEV_SEED_PASSWORD 显式指定\n"
        "# 符合强度要求的密码（≥10 位，含大小写字母/数字/符号中至少三类）\n"
        f"# 生成时间：{to_local(now_utc()).strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"种子账号密码：{password}\n"
        "适用账号：admin / liming / wangfang / zhaoqiang / sunwei / zhoutao / chenjing / zhaomin / linwei（同一密码）\n"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def init_seed(db: Session):
    """基础种子：通知模板（全环境）+ 组织 + 2 项目 + 用户 + 成员 + 知识库（幂等）。生产环境不写演示数据。"""
    # 通知模板种子在所有环境初始化（含 prod：模板是通知链路的必要基础数据，总纲 §7）
    seed_notification_templates(db)
    if settings.is_prod:
        # 生产环境只初始化必要基础数据（通知模板），不写演示业务数据
        logger.info("生产环境：跳过演示种子（用户/项目/演示数据由管理端创建）")
        return
    if db.query(User).count() > 0:
        return

    org = Organization(name="示范建设集团", code="DEMO", status="active", timezone="Asia/Shanghai")
    db.add(org)
    db.flush()

    projects = {
        "XCDS": Project(org_id=org.id, name="星辰大厦建设项目", code="XCDS",
                        project_type="building", phase="foundation", address="示范市高新区星辰路1号", status="active"),
        "BJLD": Project(org_id=org.id, name="滨江路道路改造工程", code="BJLD",
                        project_type="road", phase="structure", address="示范市滨江大道", status="active"),
    }
    for p in projects.values():
        db.add(p)
    db.flush()

    # 种子账号密码：优先取 DEV_SEED_PASSWORD（仅 dev/test 使用）；未设置则随机生成，
    # 写入 data/seed_credentials.txt 并打印到日志（P0-1：凭据不再随日志窗口丢失）
    seed_password = settings.DEV_SEED_PASSWORD.strip() or generate_strong_password()
    if not settings.DEV_SEED_PASSWORD.strip():
        cred_path = _write_seed_credentials_file(seed_password)
        logger.warning("未设置 DEV_SEED_PASSWORD，已为种子账号生成随机密码：%s（已写入 %s）",
                       seed_password, cred_path)
    password_hash = hash_password(seed_password)

    users = {}
    for username, name, phone, is_org_admin in SEED_USERS:
        u = User(
            username=username, name=name, phone=phone, org_id=org.id,
            password_hash=password_hash, active=True, status="active",
            is_org_admin=is_org_admin,
            must_change_password=False,   # 演示/种子账号无需强制改密
            password_updated_at=now_utc(),
        )
        db.add(u)
        users[username] = u
    db.flush()

    for username, proj_code, role, specialty, org_unit, is_default in SEED_MEMBERS:
        db.add(ProjectMember(
            project_id=projects[proj_code].id, user_id=users[username].id,
            role=role, specialty=specialty, org_unit=org_unit, is_default=is_default, active=True,
        ))

    # 检查项种子（挂组织；items 为空才插入，幂等）
    if db.query(InspectionItem).count() == 0:
        for name, points, basis, category, risk, types, phases in SEED_ITEMS:
            db.add(InspectionItem(
                org_id=org.id,
                name=name,
                check_points=json.dumps(points, ensure_ascii=False),
                basis=basis,
                defect_category=category,
                risk_level=risk,
                applicable_types=json.dumps(types),
                applicable_phases=json.dumps(phases),
                enabled=True,
            ))

    # SLA 规则种子（组织级默认，P05 §2.3：high=8h/medium=48h/low=168h，超期 4/24/48h 升级；项目级可覆盖）
    if db.query(SlaRule).count() == 0:
        for risk, hours, escalate in (("high", 8, 4), ("medium", 48, 24), ("low", 168, 48)):
            db.add(SlaRule(
                org_id=org.id, project_id=None, risk_level=risk, defect_category=None,
                sla_hours=hours, escalate_after_hours=escalate,
                escalate_to_role="project_admin", enabled=True,
            ))
    db.commit()
    logger.info("基础种子已写入：1 组织 + %d 项目 + %d 用户 + %d 检查项 + %d SLA 规则",
                len(projects), len(SEED_USERS), len(SEED_ITEMS), 3)


# ---------- 演示种子 ----------

# 演示附件图片来源：随包分发的真实现场照片（历史工单须显示真实照片，而非纯色/仿真占位图）
_DEMO_PHOTOS_DIR = os.path.join(os.path.dirname(__file__), "assets", "demo")

_IMAGE_MIME_BY_EXT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


def _demo_photo(index: int) -> tuple[bytes, str]:
    """从随包真实现场照片中按 index 循环取一张，返回 (bytes, mime)。"""
    files = sorted(
        name for name in os.listdir(_DEMO_PHOTOS_DIR)
        if name.lower().endswith(tuple(_IMAGE_MIME_BY_EXT))
    )
    name = files[index % len(files)]
    ext = os.path.splitext(name)[1].lower()
    with open(os.path.join(_DEMO_PHOTOS_DIR, name), "rb") as f:
        return f.read(), _IMAGE_MIME_BY_EXT[ext]

# rec4 手写第四条 AI 判别结果
DEMO_AI_4 = {
    "has_defect": True,
    "defect_desc": "一级配电箱未上锁，箱门敞开，且进出线缆防护破损",
    "severity": "中度",
    "basis": "JGJ 46《施工现场临时用电安全技术规范》：配电箱应上锁并防雨",
    "confidence": 0.85,
    "suggestion": "立即锁闭配电箱，更换破损线缆防护",
}


def _add_log(db, order_id, action, from_s, to_s, operator_id, remark, created_at):
    db.add(OrderLog(order_id=order_id, action=action, from_status=from_s, to_status=to_s,
                    operator_id=operator_id, remark=remark, created_at=created_at))


def _make_demo_attachment(db, *, org_id, project_id, biz_type, biz_id, round_no, uploader_id):
    """演示附件：随包真实现场照片写入存储适配器并落 attachments 记录（无 EXIF/水印，仅打通证据链路）"""
    data, mime = _demo_photo(biz_id or 0)
    ext = ".jpg" if mime == "image/jpeg" else ".png"
    local = to_local(now_utc())
    base_dir = f"{org_id}/{project_id}/{local.strftime('%Y')}/{local.strftime('%m')}"
    uid = uuid.uuid4().hex
    raw_key = f"{base_dir}/{uid}_raw{ext}"
    get_storage().put(raw_key, data, mime)
    att = Attachment(
        org_id=org_id,
        project_id=project_id,
        biz_type=biz_type,
        biz_id=biz_id,
        round=round_no,
        file_key=raw_key,
        raw_key=raw_key,
        file_name=f"site_photo{ext}",
        mime=mime,
        size=len(data),
        media_type="image",
        sha256=hashlib.sha256(data).hexdigest(),
        source="camera",
        watermarked=False,
        suspicious=False,
        uploader_id=uploader_id,
    )
    db.add(att)
    db.flush()
    return att


def _make_record(db, inspector, project, item, verdict, ai_result, created_at, note):
    rec = InspectionRecord(
        org_id=project.org_id,
        inspector_id=inspector.id,
        project_id=project.id,
        item_id=item.id,
        item_name=item.name,
        item_basis=item.basis,
        defect_category=item.defect_category,
        ai_result=json.dumps(ai_result, ensure_ascii=False),
        ai_mode="mock",
        human_verdict=verdict,
        note=note,
        created_at=created_at,
    )
    db.add(rec)
    db.flush()
    att = _make_demo_attachment(db, org_id=project.org_id, project_id=project.id,
                                biz_type="record", biz_id=rec.id, round_no=None, uploader_id=inspector.id)
    rec.primary_attachment_id = att.id
    db.add(AiFeedback(
        record_id=rec.id,
        org_id=project.org_id,
        project_id=project.id,
        ai_defect=bool(ai_result.get("has_defect")),
        human_defect=(verdict == "abnormal"),
        consistent=bool(ai_result.get("has_defect")) == (verdict == "abnormal"),
        created_at=created_at,
    ))
    return rec


def _make_order(db, order_no, record, project, inspector, assignee, status, created_at, deadline,
                round_no=1, feedback_text=None, closed_at=None, risk_level=None, severity=None):
    """演示工单：order_no 为 P05 新格式 WO-{项目code}-{YYYYMMDD}-{4位序号}；
    SLA 快照字段与演示 deadline 保持一致（sla_hours=created→deadline 时长）"""
    sla_hours = max(1, int((deadline - created_at).total_seconds() // 3600))
    order = RectifyOrder(
        order_no=order_no,
        org_id=project.org_id,
        project_id=project.id,
        record_id=record.id,
        inspector_id=inspector.id,
        assignee_id=assignee.id,
        problem_location=record.item_name + "：" + (json.loads(record.ai_result).get("defect_desc", "")),
        rectify_requirement=(json.loads(record.ai_result).get("suggestion", "")
                             + "。请于 48 小时内完成整改，整改后由巡查人员现场复核验收"),
        basis=record.item_basis,
        status=status,
        deadline=deadline,
        warn_at=compute_warn_at(created_at, sla_hours, None),
        sla_hours=sla_hours,
        risk_level=risk_level,   # 快照自检查项
        severity=severity,       # 快照自 AI 判定
        round=round_no,
        feedback_text=feedback_text,
        closed_at=closed_at,
        created_at=created_at,
    )
    db.add(order)
    db.flush()
    # 已提交反馈的工单：补一张整改反馈附件（biz_type=order_feedback，绑定本轮）
    if feedback_text:
        _make_demo_attachment(db, org_id=project.org_id, project_id=project.id,
                              biz_type="order_feedback", biz_id=order.id, round_no=round_no,
                              uploader_id=assignee.id)
    return order


def init_demo_data(db: Session):
    """演示种子：inspection_records 为空时执行；两项目各写业务数据以验证隔离。生产环境不执行。"""
    if settings.is_prod:
        return
    if db.query(InspectionRecord).count() > 0:
        return
    now = now_utc()

    def user_by(name):
        return db.query(User).filter(User.username == name).first()

    def project_by(code):
        return db.query(Project).filter(Project.code == code).first()

    liming, wangfang, zhaoqiang, sunwei = user_by("liming"), user_by("wangfang"), user_by("zhaoqiang"), user_by("sunwei")
    chenjing, zhaomin = user_by("chenjing"), user_by("zhaomin")
    p1, p2 = project_by("XCDS"), project_by("BJLD")
    if not all([liming, wangfang, zhaoqiang, sunwei, chenjing, zhaomin, p1, p2]):
        return

    def item_by(name):
        return db.query(InspectionItem).filter(InspectionItem.name == name).first()

    item_support = item_by("基坑支护结构完整性检查")
    item_edge = item_by("基坑临边防护检查")
    item_power = item_by("施工现场临时用电检查")
    item_road = item_by("道路路基压实度检查")
    item_face = item_by("道路面层施工质量检查")
    if not all([item_support, item_edge, item_power, item_road, item_face]):
        return

    ai_1 = {  # 裂缝
        "has_defect": True,
        "defect_desc": "照片右侧支护结构可见一道斜向裂缝，长约 40cm，缝宽目测约 2mm，位于第二道支撑与冠梁连接处附近",
        "severity": "中度", "basis": "JGJ 59-2011《建筑施工安全检查标准》：支护结构不得出现明显变形、裂缝",
        "confidence": 0.72, "suggestion": "建议设置观测标记持续监测裂缝发展，并通知专业单位复核",
    }
    ai_2 = {  # 无缺陷
        "has_defect": False,
        "defect_desc": "支护结构整体外观完好，未见明显变形、裂缝及渗漏现象，构件连接部位状态正常",
        "severity": "无", "basis": "JGJ 59-2011《建筑施工安全检查标准》表 3.13：支护结构无明显变形、裂缝",
        "confidence": 0.81, "suggestion": "维持常规巡查频次",
    }
    ai_3 = {  # 栏杆缺失
        "has_defect": True,
        "defect_desc": "临边防护栏杆缺失一段，长度约 3 米，该部位距坑边较近，存在人员坠落风险",
        "severity": "严重", "basis": "JGJ 59-2011：基坑临边应设置连续封闭防护栏杆",
        "confidence": 0.88, "suggestion": "立即封闭该区域通道，24 小时内补装防护栏杆",
    }
    ai_road = {  # 路基压实不足
        "has_defect": True,
        "defect_desc": "路基局部出现弹簧现象，压实度检测点位不合格，存在沉降风险",
        "severity": "中度", "basis": "JTG F10《公路路基施工技术规范》：路基压实度须达设计要求",
        "confidence": 0.79, "suggestion": "翻挖返工并重新分层碾压，复检合格后方可进入下道工序",
    }

    # ===== 项目 P1（星辰大厦）：4 条记录 + 3 张工单 =====
    _make_record(db, liming, p1, item_edge, "normal", ai_2, now - timedelta(days=5),
                 "防护栏杆设置规范，未见缺失，现场状态良好")

    rec2 = _make_record(db, liming, p1, item_support, "abnormal", ai_1, now - timedelta(days=5),
                        "现场复核确认支护结构存在斜向裂缝，需专业单位评估处理")
    order_a = _make_order(db, "WO-XCDS-" + to_local(now - timedelta(days=5)).strftime("%Y%m%d") + "-0001",
                          rec2, p1, liming, zhaoqiang, "closed",
                          created_at=now - timedelta(days=5), deadline=now - timedelta(days=3),
                          closed_at=now - timedelta(days=3),
                          feedback_text="已对裂缝部位注浆封闭处理，并设置观测标记，连续三日监测无扩展",
                          risk_level="high", severity="中度")
    t = order_a.created_at
    _add_log(db, order_a.id, "create", None, "pending", liming.id, f"创建工单并指派给 {zhaoqiang.name}", t)
    _add_log(db, order_a.id, "accept", "pending", "accepted", zhaoqiang.id, None, t + timedelta(hours=1))
    _add_log(db, order_a.id, "start", "accepted", "processing", zhaoqiang.id, None, t + timedelta(hours=2))
    _add_log(db, order_a.id, "feedback", "processing", "review", zhaoqiang.id, "已对裂缝部位注浆封闭处理", t + timedelta(hours=46))
    _add_log(db, order_a.id, "review_pass", "review", "closed", liming.id, "复核通过", order_a.closed_at)

    rec3 = _make_record(db, wangfang, p1, item_edge, "abnormal", ai_3, now - timedelta(days=1),
                        "东侧基坑临边栏杆确实缺失，已设置临时警戒，待补装")
    order_b = _make_order(db, "WO-XCDS-" + to_local(now - timedelta(days=1)).strftime("%Y%m%d") + "-0001",
                          rec3, p1, wangfang, sunwei, "review",
                          created_at=now - timedelta(days=1), deadline=now + timedelta(hours=47),
                          feedback_text="已按规范补装 3 米防护栏杆，并张挂安全警示标识",
                          risk_level="high", severity="严重")
    t = order_b.created_at
    _add_log(db, order_b.id, "create", None, "pending", wangfang.id, f"创建工单并指派给 {sunwei.name}", t)
    _add_log(db, order_b.id, "accept", "pending", "accepted", sunwei.id, None, t + timedelta(minutes=30))
    _add_log(db, order_b.id, "start", "accepted", "processing", sunwei.id, None, t + timedelta(hours=1))
    _add_log(db, order_b.id, "feedback", "processing", "review", sunwei.id, "已按规范补装防护栏杆", t + timedelta(hours=10))

    rec4 = _make_record(db, liming, p1, item_power, "abnormal", DEMO_AI_4, now - timedelta(days=3),
                        "一级配电箱管理不规范，存在触电与雨淋隐患，要求立即整改")
    # 剧本：整改中 + 即将超期（P1-5）——deadline 设为 6 小时后，已进入临期提醒窗口
    # （warn_at=created+75%SLA 已过）但未超期，避免演示库首屏出现"已超期 25 小时"的红牌观感
    order_c = _make_order(db, "WO-XCDS-" + to_local(now - timedelta(days=3)).strftime("%Y%m%d") + "-0001",
                          rec4, p1, liming, sunwei, "processing",
                          created_at=now - timedelta(days=3), deadline=now + timedelta(hours=6),
                          risk_level="high", severity="中度")
    t = order_c.created_at
    _add_log(db, order_c.id, "create", None, "pending", liming.id, f"创建工单并指派给 {sunwei.name}", t)
    _add_log(db, order_c.id, "accept", "pending", "accepted", sunwei.id, None, t + timedelta(hours=2))
    _add_log(db, order_c.id, "start", "accepted", "processing", sunwei.id, None, t + timedelta(hours=3))

    # ===== 项目 P2（滨江路）：2 条记录 + 1 张工单（total 与 P1 不同，用于验证隔离） =====
    _make_record(db, chenjing, p2, item_face, "normal", ai_2, now - timedelta(days=2),
                 "面层平整度抽检合格，接缝处理规范")
    rec_p2 = _make_record(db, chenjing, p2, item_road, "abnormal", ai_road, now - timedelta(days=2),
                          "K2+300 路段路基压实度检测不合格，需返工")
    order_d = _make_order(db, "WO-BJLD-" + to_local(now - timedelta(days=2)).strftime("%Y%m%d") + "-0001",
                          rec_p2, p2, chenjing, zhaomin, "pending",
                          created_at=now - timedelta(days=2), deadline=now + timedelta(hours=46),
                          risk_level="high", severity="中度")
    _add_log(db, order_d.id, "create", None, "pending", chenjing.id, f"创建工单并指派给 {zhaomin.name}", order_d.created_at)

    db.commit()
    logger.info("演示种子已写入：P1 4 记录/3 工单，P2 2 记录/1 工单")


def init_admin_from_env(db: Session):
    """云托管部署首启初始化：空库时按环境变量创建首个组织/项目/管理员（一次性，幂等）。
    对应本地部署的 scripts/bootstrap_admin.py（交互式）；容器内无法交互，改由环境变量驱动。
    全量配置且库中无任何账号时才执行；密码不落日志。"""
    username = settings.ADMIN_INIT_USERNAME.strip()
    password = settings.ADMIN_INIT_PASSWORD
    org_name = settings.ADMIN_INIT_ORG.strip()
    project_name = settings.ADMIN_INIT_PROJECT.strip()
    if not (username and password and org_name and project_name):
        return
    if db.query(User).count() > 0:
        return
    from .security import validate_password_strength

    try:
        validate_password_strength(password)
    except ValueError as exc:
        logger.error("ADMIN_INIT_PASSWORD 强度不足，跳过管理员初始化：%s", exc)
        return

    org = Organization(name=org_name, code="INITIAL", status="active", timezone=settings.APP_TIMEZONE)
    db.add(org)
    db.flush()
    user = User(username=username, name=username, password_hash=hash_password(password),
                org_id=org.id, is_org_admin=True, must_change_password=True,
                active=True, status="active", password_updated_at=now_utc())
    db.add(user)
    db.flush()
    project = Project(org_id=org.id, name=project_name, code="P1",
                      project_type="building", phase="foundation", status="active")
    db.add(project)
    db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role="project_admin", is_default=True))
    db.commit()
    logger.info("管理员初始化完成（org=%s project=%s user=%s），首次登录将强制改密", org.id, project.id, username)
