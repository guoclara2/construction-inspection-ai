"""p04 photo evidence chain

Revision ID: 2030614011f0
Revises: b04b75072f3a
Create Date: 2026-09-05 20:29:41.060711

P04 照片证据链：新增 attachments 表（原图/水印图双对象 + EXIF/GPS/SHA256/存证异常标记），
inspection_records 以 primary_attachment_id 关联主照片（删除 photo_path 单字段），
rectify_orders 反馈照片改由 attachments 承载（删除 feedback_photo 单字段）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2030614011f0'
down_revision: Union[str, None] = 'b04b75072f3a'
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


def _has_fkey(table: str, name: str) -> bool:
    insp = _insp()
    return insp.has_table(table) and any(f.get("name") == name for f in insp.get_foreign_keys(table))


def _create_table_if_missing(name: str, *args, **kwargs):
    if not _has_table(name):
        op.create_table(name, *args, **kwargs)


def upgrade() -> None:
    _create_table_if_missing('attachments',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('org_id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('biz_type', sa.String(length=20), nullable=False),
    sa.Column('biz_id', sa.Integer(), nullable=True),
    sa.Column('round', sa.Integer(), nullable=True),
    sa.Column('file_key', sa.String(length=300), nullable=False),
    sa.Column('raw_key', sa.String(length=300), nullable=False),
    sa.Column('file_name', sa.String(length=200), nullable=False),
    sa.Column('mime', sa.String(length=50), nullable=False),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.Column('media_type', sa.String(length=10), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('width', sa.Integer(), nullable=True),
    sa.Column('height', sa.Integer(), nullable=True),
    sa.Column('duration', sa.Integer(), nullable=True),
    sa.Column('exif_time', sa.DateTime(timezone=True), nullable=True),
    sa.Column('gps_lat', sa.Numeric(precision=10, scale=6), nullable=True),
    sa.Column('gps_lng', sa.Numeric(precision=10, scale=6), nullable=True),
    sa.Column('gps_accuracy', sa.Numeric(precision=8, scale=2), nullable=True),
    sa.Column('source', sa.String(length=10), nullable=False),
    sa.Column('watermarked', sa.Boolean(), nullable=False),
    sa.Column('suspicious', sa.Boolean(), nullable=False),
    sa.Column('uploader_id', sa.Integer(), nullable=False),
    sa.Column('sort_no', sa.Integer(), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], name='fk_attachments_org_id'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], name='fk_attachments_project_id'),
    sa.ForeignKeyConstraint(['uploader_id'], ['users.id'], name='fk_attachments_uploader_id'),
    sa.PrimaryKeyConstraint('id')
    )
    if not _has_index('attachments', 'ix_attachments_biz'):
        op.create_index('ix_attachments_biz', 'attachments', ['biz_type', 'biz_id'], unique=False)

    with op.batch_alter_table('inspection_records', schema=None) as batch_op:
        if not _has_column('inspection_records', 'primary_attachment_id'):
            batch_op.add_column(sa.Column('primary_attachment_id', sa.Integer(), nullable=True))
        if not _has_fkey('inspection_records', 'fk_records_primary_attachment'):
            batch_op.create_foreign_key('fk_records_primary_attachment', 'attachments', ['primary_attachment_id'], ['id'])
        if _has_column('inspection_records', 'photo_path'):
            batch_op.drop_column('photo_path')

    with op.batch_alter_table('rectify_orders', schema=None) as batch_op:
        if _has_column('rectify_orders', 'feedback_photo'):
            batch_op.drop_column('feedback_photo')


def downgrade() -> None:
    with op.batch_alter_table('rectify_orders', schema=None) as batch_op:
        batch_op.add_column(sa.Column('feedback_photo', sa.VARCHAR(length=200), nullable=True))

    with op.batch_alter_table('inspection_records', schema=None) as batch_op:
        batch_op.drop_column('primary_attachment_id')
        batch_op.add_column(sa.Column('photo_path', sa.VARCHAR(length=200), nullable=False,
                                      server_default=''))

    op.drop_index('ix_attachments_biz', table_name='attachments')
    op.drop_table('attachments')
