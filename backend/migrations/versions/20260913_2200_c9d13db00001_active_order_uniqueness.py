"""Enforce one active order per record on all supported databases.

Revision ID: c9d13db00001
Revises: 50b77a799c5c
"""
from alembic import op
import sqlalchemy as sa

revision = 'c9d13db00001'
down_revision = '50b77a799c5c'
branch_labels = None
depends_on = None


def upgrade():
    # Do not silently delete historical duplicates. Resolve them explicitly before retrying.
    duplicate = op.get_bind().execute(sa.text(
        "SELECT record_id FROM rectify_orders WHERE status <> 'cancelled' "
        "GROUP BY record_id HAVING COUNT(*) > 1 LIMIT 1")).first()
    if duplicate:
        raise RuntimeError('Duplicate active orders exist; reconcile before migration')
    inspector = sa.inspect(op.get_bind())
    indexes = {row['name'] for row in inspector.get_indexes('rectify_orders')}
    columns = {row['name'] for row in inspector.get_columns('rectify_orders')}
    if 'active_record_id' not in columns:
        # SQLite cannot ALTER ADD a STORED generated column; batch reconstructs safely.
        with op.batch_alter_table('rectify_orders', recreate='always' if op.get_bind().dialect.name == 'sqlite' else 'auto') as batch:
            batch.add_column(sa.Column('active_record_id', sa.Integer(), sa.Computed(
                "CASE WHEN status <> 'cancelled' THEN record_id ELSE NULL END", persisted=True), nullable=True))
    if 'uq_order_active_record' in indexes:
        op.drop_index('uq_order_active_record', table_name='rectify_orders')
    op.create_index('uq_order_active_record', 'rectify_orders', ['active_record_id'], unique=True)
    # Older SQLite history missed this lookup index, while the MySQL/PostgreSQL
    # initial schema already contained it. Keep metadata and every real schema aligned.
    if 'ix_orders_record' not in indexes:
        op.create_index('ix_orders_record', 'rectify_orders', ['record_id'], unique=False)


def downgrade():
    op.drop_index('uq_order_active_record', table_name='rectify_orders')
    if op.get_bind().dialect.name == 'sqlite':
        op.drop_index('ix_orders_record', table_name='rectify_orders')
    with op.batch_alter_table('rectify_orders') as batch:
        batch.drop_column('active_record_id')
    if op.get_bind().dialect.name in ('postgresql', 'sqlite'):
        op.create_index('uq_order_active_record', 'rectify_orders', ['record_id'], unique=True,
            postgresql_where=sa.text("status <> 'cancelled'"), sqlite_where=sa.text("status <> 'cancelled'"))
