"""p06 overdue governance and notifications

Revision ID: b7e3d5a20c91
Revises: a5f2c8d91e47
Create Date: 2026-09-05 23:00:00.000000

P06 超期治理与通知触达：
1. messages → notifications 重构（总纲 §4.7，允许重建：旧演示消息不迁移，表结构全新建立）；
2. 新表 notification_templates（模板/通道集合/微信与短信模板 ID，总纲 §7）；
3. 新表 wx_subscribe_auth（用户订阅消息授权，P06 §2.4）；
4. sla_rules 增加 urge_interval_hours（催办限流窗口，P06 §2.3）；
5. order_logs.operator_id 改可空（NULL=系统调度动作，如超期扫描写 escalate，总纲 §4.4 P06 修订）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e3d5a20c91'
down_revision: Union[str, None] = 'a5f2c8d91e47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


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
    # ---- 1. messages → notifications（总纲第 2 章允许重建，旧演示消息随旧表丢弃）----
    if _has_table('messages'):
        op.drop_table('messages')

    _create_table_if_missing('notifications',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('org_id', sa.Integer(), nullable=True),
    sa.Column('project_id', sa.Integer(), nullable=True),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=100), nullable=False),
    sa.Column('content', sa.String(length=500), nullable=False),
    sa.Column('biz_type', sa.String(length=20), nullable=True),
    sa.Column('biz_id', sa.Integer(), nullable=True),
    sa.Column('channel', sa.String(length=10), nullable=False),
    sa.Column('template_code', sa.String(length=50), nullable=True),
    sa.Column('status', sa.String(length=10), server_default='pending', nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('retry_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('error', sa.String(length=500), nullable=True),
    sa.Column('dedup_key', sa.String(length=100), nullable=True),
    sa.Column('read', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name='fk_notifications_org_id'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name='fk_notifications_project_id'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_notifications_user_id'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('dedup_key')
    )
    _create_index_if_missing('notifications', 'ix_notifications_user_read', ['user_id', 'read', 'id'])
    _create_index_if_missing('notifications', 'ix_notifications_status', ['status'])

    # ---- 2. notification_templates ----
    _create_table_if_missing('notification_templates',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('code', sa.String(length=50), nullable=False),
    sa.Column('channel', sa.String(length=50), nullable=False),
    sa.Column('title_tpl', sa.String(length=100), nullable=False),
    sa.Column('content_tpl', sa.String(length=500), nullable=False),
    sa.Column('wx_template_id', sa.String(length=64), nullable=True),
    sa.Column('sms_template_code', sa.String(length=50), nullable=True),
    sa.Column('enabled', sa.Boolean(), server_default=sa.true(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )

    # ---- 3. wx_subscribe_auth ----
    _create_table_if_missing('wx_subscribe_auth',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('template_code', sa.String(length=50), nullable=False),
    sa.Column('accepted', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('subscribed_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_wx_subscribe_auth_user_id'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'template_code', name='uq_wx_subscribe_user_template')
    )

    # ---- 4. sla_rules：催办限流窗口（可空，NULL=沿用默认 1 小时）----
    with op.batch_alter_table('sla_rules', schema=None) as batch_op:
        if not _has_column('sla_rules', 'urge_interval_hours'):
            batch_op.add_column(sa.Column('urge_interval_hours', sa.Integer(), nullable=True))

    # ---- 5. order_logs.operator_id 改可空（系统调度动作无人工操作人）----
    with op.batch_alter_table('order_logs', schema=None) as batch_op:
        batch_op.alter_column('operator_id', existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table('order_logs', schema=None) as batch_op:
        batch_op.alter_column('operator_id', existing_type=sa.Integer(), nullable=False)

    with op.batch_alter_table('sla_rules', schema=None) as batch_op:
        batch_op.drop_column('urge_interval_hours')

    op.drop_table('wx_subscribe_auth')
    op.drop_table('notification_templates')

    op.drop_index('ix_notifications_status', table_name='notifications')
    op.drop_index('ix_notifications_user_read', table_name='notifications')
    op.drop_table('notifications')

    # 还原 V1 messages 结构（演示数据已丢弃，仅恢复表壳）
    op.create_table('messages',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('org_id', sa.Integer(), nullable=True),
    sa.Column('project_id', sa.Integer(), nullable=True),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=100), nullable=False),
    sa.Column('content', sa.String(length=500), nullable=False),
    sa.Column('order_id', sa.Integer(), nullable=True),
    sa.Column('read', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name='fk_messages_org_id'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name='fk_messages_project_id'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='fk_messages_user_id'),
    sa.ForeignKeyConstraint(['order_id'], ['rectify_orders.id'], name='fk_messages_order_id'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_messages_user_read', 'messages', ['user_id', 'read', 'id'])
