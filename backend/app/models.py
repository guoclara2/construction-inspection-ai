# ORM 模型：组织—项目—成员三层多租户 + 业务表，字段契约见总纲第 4 章
# 时间列一律 timezone-aware，默认值由数据库 func.now() 生成（消灭 naive 本地时间 D9）
# P05：高频查询索引（总纲 §10）与部分唯一索引（一记录至多一张有效工单）随模型声明，
# 与 Alembic 迁移逐字一致（alembic check 依据 metadata 比对）。
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

# 跨库 JSON：PostgreSQL 用 JSONB，SQLite 用 JSON
JSONType = JSON().with_variant(JSONB, "postgresql")

from .database import Base


class Organization(Base):
    """组织（多租户顶层）：一个组织下辖多个项目与成员，知识库按组织隔离"""

    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)  # active/disabled
    timezone: Mapped[str] = mapped_column(String(50), default="Asia/Shanghai", nullable=False)


class User(Base):
    """用户：不再直接挂 role/specialty/project_id（迁至 project_members），只保留账号与组织归属"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    wx_openid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # —— 组织归属与账号安全 ——
    is_org_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)  # active/disabled
    wx_unionid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    password_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    code: Mapped[str | None] = mapped_column(String(50), nullable=True)  # 组织内唯一（见 UniqueConstraint）
    project_type: Mapped[str] = mapped_column(String(20), nullable=False)  # building/road/tunnel/landscape
    phase: Mapped[str] = mapped_column(String(20), nullable=False)  # foundation/structure/mep/decoration
    address: Mapped[str | None] = mapped_column(String(200), nullable=True)
    lng: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    lat: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    planned_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)  # active/paused/closed

    __table_args__ = (UniqueConstraint("org_id", "code", name="uq_project_org_code"),)


class ProjectMember(Base):
    """项目成员：取代 users 上的 role/specialty/project_id。一人一项目多角色 → 多行"""

    __tablename__ = "project_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    # inspector/rectifier/reviewer/project_admin/viewer
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    specialty: Mapped[str | None] = mapped_column(String(20), nullable=True)  # civil/mech/deco/safety
    org_unit: Mapped[str | None] = mapped_column(String(100), nullable=True)  # 所属单位（总包/监理/分包）
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (UniqueConstraint("project_id", "user_id", "role", name="uq_member_project_user_role"),)


class InspectionItem(Base):
    __tablename__ = "inspection_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # org_id 可空：NULL 表示平台内置模板，对所有组织可见
    org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True)
    code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    check_points: Mapped[str] = mapped_column(Text, nullable=False)  # JSON 数组字符串
    basis: Mapped[str] = mapped_column(String(500), nullable=False)
    defect_category: Mapped[str] = mapped_column(String(20), nullable=False)  # structural/protection/installation/material/safety
    risk_level: Mapped[str] = mapped_column(String(10), nullable=False)  # high/medium/low
    applicable_types: Mapped[str] = mapped_column(Text, nullable=False)  # JSON 数组字符串
    applicable_phases: Mapped[str] = mapped_column(Text, nullable=False)  # JSON 数组字符串
    publication: Mapped[str] = mapped_column(String(20), default="legacy", nullable=False, server_default="legacy")
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", name="fk_item_approved_by"))
    default_sla_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 检查项默认整改时限
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class InspectionRecord(Base):
    __tablename__ = "inspection_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    inspector_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)  # 强制过滤维度
    item_id: Mapped[int] = mapped_column(ForeignKey("inspection_items.id"), nullable=False)
    item_name: Mapped[str] = mapped_column(String(200), nullable=False)  # 快照冗余
    item_basis: Mapped[str] = mapped_column(String(500), nullable=False)  # 快照冗余
    defect_category: Mapped[str] = mapped_column(String(20), nullable=False)  # 快照冗余
    # 主照片改由 attachments 承载（P04）：photo_path 单字段移除，列表缩略图取 primary_attachment_id
    primary_attachment_id: Mapped[int | None] = mapped_column(ForeignKey("attachments.id"), nullable=True)
    ai_result: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON 字符串
    ai_mode: Mapped[str | None] = mapped_column(String(10), nullable=True)  # real/mock
    human_verdict: Mapped[str | None] = mapped_column(String(20), nullable=True)  # normal/abnormal，非空即已完成确认
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    item_snapshot: Mapped[dict | None] = mapped_column(JSONType)
    location: Mapped[dict | None] = mapped_column(JSONType)
    request_key: Mapped[str | None] = mapped_column(String(64))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    confirm_key: Mapped[str | None] = mapped_column(String(64))
    confirm_hash: Mapped[str | None] = mapped_column(String(64))

    # 列表/看板高频查询索引（总纲 §10 / P05 §2.5）：
    # 普通列声明（非 text DESC 表达式）——PostgreSQL 反向扫描同样覆盖 ORDER BY created_at DESC，
    # 且与数据库反射一致，alembic check 可通过
    __table_args__ = (
        UniqueConstraint("project_id", "inspector_id", "request_key", name="uq_record_request"),
        Index("ix_records_project_created", "project_id", "created_at"),
        Index("ix_records_project_verdict", "project_id", "human_verdict"),
        Index("ix_records_project_inspector", "project_id", "inspector_id", "created_at"),
    )


class RectifyOrder(Base):
    __tablename__ = "rectify_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)  # 强制过滤维度
    order_no: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    record_id: Mapped[int] = mapped_column(ForeignKey("inspection_records.id"), nullable=False)
    inspector_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    assignee_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    problem_location: Mapped[str] = mapped_column(Text, nullable=False)
    rectify_requirement: Mapped[str] = mapped_column(Text, nullable=False)
    basis: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)  # pending/accepted/processing/review/closed
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    round: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # 整改反馈照片改由 attachments(biz_type='order_feedback', biz_id=order_id, round=N) 承载（P04）
    feedback_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_result: Mapped[str | None] = mapped_column(String(20), nullable=True)  # pass/reject
    review_comment: Mapped[str | None] = mapped_column(String(500), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # —— P05：SLA 快照（时限一旦写入即固化，规则后续修改不影响存量工单）——
    risk_level: Mapped[str | None] = mapped_column(String(10), nullable=True)   # 快照自检查项 high/medium/low
    severity: Mapped[str | None] = mapped_column(String(20), nullable=True)     # 快照自 AI/人工判定
    sla_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)       # 本单采用的整改时限（小时）
    warn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # 临期提醒时间
    # —— P05：乐观锁（每次流转 +1；接口可选携带 expected_version 校验）——
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # NULL for cancelled orders permits replacement; server-maintained on every write.
    active_record_id: Mapped[int | None] = mapped_column(Integer,
        Computed("CASE WHEN status <> 'cancelled' THEN record_id ELSE NULL END", persisted=True))

    # 监督/待办/看板高频查询索引 + 一记录至多一张有效工单的部分唯一索引（P05 §2.1/§2.5）
    __table_args__ = (
        Index("ix_orders_project_status", "project_id", "status"),
        Index("ix_orders_project_deadline", "project_id", "deadline"),
        Index("ix_orders_record", "record_id"),
        Index("ix_orders_assignee_status", "assignee_id", "status"),
        Index("ix_orders_inspector_status", "inspector_id", "status"),
        Index("uq_order_active_record", "active_record_id", unique=True),
    )


class OrderLog(Base):
    __tablename__ = "order_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    order_id: Mapped[int] = mapped_column(ForeignKey("rectify_orders.id"), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    # P06：可空（NULL=系统调度动作，如超期扫描写 escalate）；人工操作仍必填
    operator_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False)  # create/accept/start/feedback/review_pass/review_reject/urge/escalate
    remark: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (Index("ix_order_logs_order", "order_id", "id"),)


class Notification(Base):
    """多通道通知（P06 对 V1 messages 的重构，总纲 §4.7）：一次业务事件 × 每通道一行记录"""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)  # 防跨项目串扰
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    content: Mapped[str] = mapped_column(String(500), nullable=False)
    biz_type: Mapped[str | None] = mapped_column(String(20), nullable=True)   # order/plan/...
    biz_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    channel: Mapped[str] = mapped_column(String(10), nullable=False)          # inapp/wechat/sms
    template_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="pending", nullable=False)  # pending/sent/failed/skipped
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # 去重键（唯一）：同一事件重复触发不重复发送；调度器扫描任务按业务语义构造（如 ORDER_OVERDUE:{oid}:{day}）
    dedup_key: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # 仅 inapp 有意义

    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_token: Mapped[str | None] = mapped_column(String(40))

    # 未读列表高频索引（总纲 §10）+ 投递/重试扫描索引；普通列声明：PG 反向扫描覆盖 ORDER BY id DESC
    __table_args__ = (
        Index("ix_notifications_user_read", "user_id", "read", "id"),
        Index("ix_notifications_status", "status"),
    )


class NotificationTemplate(Base):
    """通知模板（总纲 §4.7）：channel 为逗号分隔的适用通道集合，是 resolve_channels 的数据源"""

    __tablename__ = "notification_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    channel: Mapped[str] = mapped_column(String(50), nullable=False)          # 如 inapp,wechat / inapp,wechat,sms
    title_tpl: Mapped[str] = mapped_column(String(100), nullable=False)
    content_tpl: Mapped[str] = mapped_column(String(500), nullable=False)
    wx_template_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sms_template_code: Mapped[str | None] = mapped_column(String(50), nullable=True)  # 短信服务商模板 ID（仅短信通道模板需要）
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class WxSubscribeAuth(Base):
    """用户对微信订阅消息模板的授权记录（P06 §2.4）：后端只对已授权用户发订阅消息"""

    __tablename__ = "wx_subscribe_auth"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    template_code: Mapped[str] = mapped_column(String(50), nullable=False)    # 对应 notification_templates.code
    accepted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # 本次授权结果（accept/reject/ban）
    subscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "template_code", name="uq_wx_subscribe_user_template"),
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)  # sha256(token)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    replaced_by: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 轮换后新 token 的 id
    ua: Mapped[str | None] = mapped_column(String(300), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    org_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    before: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)
    result: Mapped[str] = mapped_column(String(20), default="success", nullable=False)  # success/denied/error

    # 普通列声明：PG 反向扫描覆盖 ORDER BY created_at DESC，且与反射一致（alembic check 可通过）
    __table_args__ = (Index("ix_audit_logs_project_created", "project_id", "created_at"),)


class Attachment(Base):
    """现场证据附件：取代所有单图字段。存原图（raw_key，不可覆盖）+ 水印图（file_key，对外呈现）。
    含 EXIF 拍摄时间 / GPS / SHA256 / 存证异常标记，构成可追溯的证据对象（总纲 §4.5 / §6.3）。"""

    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), nullable=False)
    biz_type: Mapped[str] = mapped_column(String(20), nullable=False)  # record/order_feedback/order_extra
    biz_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 先传后绑；绑定后不可改
    round: Mapped[int | None] = mapped_column(Integer, nullable=True)  # order_feedback 属第几轮
    file_key: Mapped[str] = mapped_column(String(300), nullable=False)  # 水印图（对外呈现）
    raw_key: Mapped[str] = mapped_column(String(300), nullable=False)   # 原图（只写一次，不覆盖）
    file_name: Mapped[str] = mapped_column(String(200), nullable=False)
    mime: Mapped[str] = mapped_column(String(50), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    media_type: Mapped[str] = mapped_column(String(10), nullable=False)  # image/audio/video
    upload_key: Mapped[str | None] = mapped_column(String(64))
    evidence: Mapped[dict | None] = mapped_column(JSONType)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exif_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    gps_lat: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    gps_lng: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    gps_accuracy: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    source: Mapped[str] = mapped_column(String(10), default="unknown", nullable=False)  # camera/album/unknown
    watermarked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    suspicious: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    uploader_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    sort_no: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # 软删

    # 附件按业务检索的高频索引（总纲 §10）
    __table_args__ = (
        UniqueConstraint("project_id", "uploader_id", "upload_key", name="uq_attachment_upload"),
        Index("ix_attachments_biz", "biz_type", "biz_id"),
    )


class AiFeedback(Base):
    __tablename__ = "ai_feedbacks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)
    record_id: Mapped[int] = mapped_column(ForeignKey("inspection_records.id"), nullable=False)
    ai_defect: Mapped[bool] = mapped_column(Boolean, nullable=False)
    human_defect: Mapped[bool] = mapped_column(Boolean, nullable=False)
    consistent: Mapped[bool] = mapped_column(Boolean, nullable=False)


class SlaRule(Base):
    """整改时限规则：project_id 空=组织级默认，非空=项目级覆盖（总纲 §4.4）"""

    __tablename__ = "sla_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"), nullable=True)  # 空=组织级
    risk_level: Mapped[str] = mapped_column(String(10), nullable=False)  # high/medium/low
    defect_category: Mapped[str | None] = mapped_column(String(20), nullable=True)  # 空=该风险等级全部类别
    sla_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    warn_ratio: Mapped[Decimal] = mapped_column(Numeric(4, 2), default=Decimal("0.75"), nullable=False)
    escalate_after_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    escalate_to_role: Mapped[str | None] = mapped_column(String(20), nullable=True)  # project_admin/org_admin
    urge_interval_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 催办限流窗口（P06，默认 1h）
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class OrderNoSeq(Base):
    """工单号每日序列：(project_id, date) 原子自增取号，消灭 count()+1 并发撞号（D3）"""

    __tablename__ = "order_no_seq"

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    date: Mapped[str] = mapped_column(String(8), primary_key=True)  # YYYYMMDD（项目本地时区）
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

from . import p1_models  # register operational tables
