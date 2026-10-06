"""p05 data correctness and concurrency

Revision ID: a5f2c8d91e47
Revises: 2030614011f0
Create Date: 2026-09-05 22:00:00.000000

P05 数据正确性与并发：
1. 新表 sla_rules（整改时限规则，org/project 两级，总纲 §4.4）；
2. 新表 order_no_seq（工单号每日序列，消灭 count()+1 并发撞号 D3）；
3. rectify_orders 增加 SLA 快照列（risk_level/severity/sla_hours/warn_at）与乐观锁 version 列（D5/D10）；
4. 部分唯一索引 uq_order_active_record：一记录至多一张有效工单（D4 数据库层兜底）；
5. 高频查询索引（总纲 §10 / P05 §2.5）——messages 为 notifications（P06）前身，先建等价索引。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a5f2c8d91e47'
down_revision: Union[str, None] = '2030614011f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 部分唯一索引谓词：与 models.py __table_args__ 逐字一致（alembic check 依据 metadata 比对）
_ACTIVE_PREDICATE = "status <> 'cancelled'"


# ---- 幂等护栏（自愈迁移）：对象已存在则跳过对应 DDL，详见 initial 迁移头部说明 ----
def _insp():
    return sa.inspect(op.get_bind())


def _has_table(name: str) -> bool:
    return _insp().has_table(name)


def _has_column(table: str, column: str) -> bool:
    insp = _insp()
    return insp.has_table(table) and any(c["name"] == column for c in insp.get_columns(table))


def _has_index(table: str, index: str) -> bool:
    # 索引或唯一约束（MySQL 唯一约束即唯一索引，两处都查）
    insp = _insp()
    if not insp.has_table(table):
        return False
    names = {i["name"] for i in insp.get_indexes(table)}
    names |= {u["name"] for u in insp.get_unique_constraints(table)}
    return index in names


def _create_table_if_missing(name: str, *args, **kwargs):
    if not _has_table(name):
        op.create_table(name, *args, **kwargs)


def _create_index_if_missing(table: str, index: str, columns, **kwargs):
    if _has_table(table) and not _has_index(table, index):
        op.create_index(index, table, columns, **kwargs)


def upgrade() -> None:
    # ---- 1. sla_rules ----
    _create_table_if_missing('sla_rules',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('org_id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=True),
    sa.Column('risk_level', sa.String(length=10), nullable=False),
    sa.Column('defect_category', sa.String(length=20), nullable=True),
    sa.Column('sla_hours', sa.Integer(), nullable=False),
    sa.Column('warn_ratio', sa.Numeric(precision=4, scale=2), server_default='0.75', nullable=False),
    sa.Column('escalate_after_hours', sa.Integer(), nullable=False),
    sa.Column('escalate_to_role', sa.String(length=20), nullable=True),
    sa.Column('enabled', sa.Boolean(), server_default=sa.true(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name='fk_sla_rules_org_id'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name='fk_sla_rules_project_id'),
    sa.PrimaryKeyConstraint('id')
    )

    # ---- 2. order_no_seq ----
    _create_table_if_missing('order_no_seq',
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('date', sa.String(length=8), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False, server_default='0'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name='fk_order_no_seq_project_id'),
    sa.PrimaryKeyConstraint('project_id', 'date')
    )

    # ---- 3. rectify_orders 新列（SLA 快照 + 乐观锁）----
    with op.batch_alter_table('rectify_orders', schema=None) as batch_op:
        if not _has_column('rectify_orders', 'risk_level'):
            batch_op.add_column(sa.Column('risk_level', sa.String(length=10), nullable=True))
        if not _has_column('rectify_orders', 'severity'):
            batch_op.add_column(sa.Column('severity', sa.String(length=20), nullable=True))
        if not _has_column('rectify_orders', 'sla_hours'):
            batch_op.add_column(sa.Column('sla_hours', sa.Integer(), nullable=True))
        if not _has_column('rectify_orders', 'warn_at'):
            batch_op.add_column(sa.Column('warn_at', sa.DateTime(timezone=True), nullable=True))
        if not _has_column('rectify_orders', 'version'):
            batch_op.add_column(sa.Column('version', sa.Integer(), nullable=False, server_default='0'))

    # ---- 4. 部分唯一索引：一记录至多一张有效工单（作废后允许重新建单）----
    # MySQL 不支持部分索引（where 谓词被忽略会退化为全量唯一，阻断「作废后重建单」）：
    # MySQL 降级为普通索引，唯一性由服务层事务内校验兜底；PG/SQLite 保持部分唯一索引
    if op.get_bind().dialect.name in ("postgresql", "sqlite"):
        _create_index_if_missing(
            'rectify_orders', 'uq_order_active_record', ['record_id'], unique=True,
            postgresql_where=sa.text(_ACTIVE_PREDICATE), sqlite_where=sa.text(_ACTIVE_PREDICATE),
        )
    else:
        _create_index_if_missing('rectify_orders', 'ix_orders_record', ['record_id'])

    # ---- 5. 高频查询索引（普通列声明：PG 反向扫描覆盖 DESC 排序，且与反射一致保证 alembic check 通过）----
    _create_index_if_missing('inspection_records', 'ix_records_project_created', ['project_id', 'created_at'])
    _create_index_if_missing('inspection_records', 'ix_records_project_verdict', ['project_id', 'human_verdict'])
    _create_index_if_missing('inspection_records', 'ix_records_project_inspector', ['project_id', 'inspector_id', 'created_at'])

    # ---- rectify_orders ----
    _create_index_if_missing('rectify_orders', 'ix_orders_project_status', ['project_id', 'status'])
    _create_index_if_missing('rectify_orders', 'ix_orders_project_deadline', ['project_id', 'deadline'])
    _create_index_if_missing('rectify_orders', 'ix_orders_assignee_status', ['assignee_id', 'status'])
    _create_index_if_missing('rectify_orders', 'ix_orders_inspector_status', ['inspector_id', 'status'])

    # ---- messages（notifications 的 P06 前身，先建等价索引）----
    _create_index_if_missing('messages', 'ix_messages_user_read', ['user_id', 'read', 'id'])

    # ---- audit_logs / order_logs ----
    _create_index_if_missing('audit_logs', 'ix_audit_logs_project_created', ['project_id', 'created_at'])
    _create_index_if_missing('order_logs', 'ix_order_logs_order', ['order_id', 'id'])

    # 注：attachments(biz_type, biz_id) 索引 ix_attachments_biz 已由 P04 迁移建立，不重复创建。


def downgrade() -> None:
    op.drop_index('ix_order_logs_order', table_name='order_logs')
    op.drop_index('ix_audit_logs_project_created', table_name='audit_logs')
    op.drop_index('ix_messages_user_read', table_name='messages')
    op.drop_index('ix_orders_inspector_status', table_name='rectify_orders')
    op.drop_index('ix_orders_assignee_status', table_name='rectify_orders')
    op.drop_index('ix_orders_project_deadline', table_name='rectify_orders')
    op.drop_index('ix_orders_project_status', table_name='rectify_orders')
    op.drop_index('ix_records_project_inspector', table_name='inspection_records')
    op.drop_index('ix_records_project_verdict', table_name='inspection_records')
    op.drop_index('ix_records_project_created', table_name='inspection_records')
    if op.get_bind().dialect.name in ("postgresql", "sqlite"):
        op.drop_index('uq_order_active_record', table_name='rectify_orders')
    else:
        op.drop_index('ix_orders_record', table_name='rectify_orders')

    with op.batch_alter_table('rectify_orders', schema=None) as batch_op:
        batch_op.drop_column('version')
        batch_op.drop_column('warn_at')
        batch_op.drop_column('sla_hours')
        batch_op.drop_column('severity')
        batch_op.drop_column('risk_level')

    op.drop_table('order_no_seq')
    op.drop_table('sla_rules')
