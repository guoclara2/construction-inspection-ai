"""Operational records. Business changes and their evidence commit together."""
from datetime import datetime, date
from sqlalchemy import String, Integer, Text, DateTime, Date, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from .database import Base
from .models import JSONType

class AiJob(Base):
    __tablename__ = 'ai_jobs'
    id: Mapped[int] = mapped_column(primary_key=True)
    record_id: Mapped[int] = mapped_column(ForeignKey('inspection_records.id'), unique=True)
    status: Mapped[str] = mapped_column(String(20), default='pending', index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    token: Mapped[str | None] = mapped_column(String(40))
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class AiBudget(Base):
    __tablename__ = 'ai_budgets'
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    calls: Mapped[int] = mapped_column(default=0)

class AiUsage(Base):
    __tablename__ = 'ai_usage'
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id'), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    purpose: Mapped[str] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20))
    input_tokens: Mapped[int | None] = mapped_column()
    output_tokens: Mapped[int | None] = mapped_column()
    cost: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class FeedbackRound(Base):
    __tablename__ = 'feedback_rounds'
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey('rectify_orders.id'))
    round: Mapped[int] = mapped_column()
    text: Mapped[str] = mapped_column(Text)
    submitter_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[str | None] = mapped_column(String(20))
    comment: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint('order_id', 'round', name='uq_feedback_round'),)

class KnowledgeRevision(Base):
    __tablename__ = 'knowledge_revisions'
    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey('inspection_items.id'))
    version: Mapped[int] = mapped_column()
    snapshot: Mapped[dict] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint('item_id', 'version', name='uq_knowledge_revision'),)

class InspectionPlan(Base):
    __tablename__ = 'inspection_plans'
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id'), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey('inspection_items.id'))
    inspector_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    name: Mapped[str] = mapped_column(String(100))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    interval_days: Mapped[int] = mapped_column()
    enabled: Mapped[bool] = mapped_column(default=True)

class PlanTask(Base):
    __tablename__ = 'plan_tasks'
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey('inspection_plans.id'))
    project_id: Mapped[int] = mapped_column(ForeignKey('projects.id'), index=True)
    inspector_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    scheduled_date: Mapped[date] = mapped_column(Date)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default='pending')
    record_id: Mapped[int | None] = mapped_column(ForeignKey('inspection_records.id'), unique=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint('plan_id', 'scheduled_date', name='uq_plan_task_day'),)

class DeadlineRequest(Base):
    __tablename__ = 'deadline_requests'
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey('rectify_orders.id'), index=True)
    requester_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default='pending')
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
