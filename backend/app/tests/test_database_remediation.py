from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace
from sqlalchemy.dialects import mysql
from app.database import SessionLocal
from app.models import InspectionItem
from app.p1_models import KnowledgeRevision, InspectionPlan, PlanTask
from app.services.order_flow import next_order_no
from app.services.operations import generate_tasks
from app.utils import now_utc


def test_mysql_order_sequence_compiles_without_returning():
    statements = []
    class DB:
        bind = SimpleNamespace(dialect=mysql.dialect())
        def execute(self, statement):
            sql = str(statement.compile(dialect=self.bind.dialect))
            statements.append(sql)
            assert 'RETURNING' not in sql
            return SimpleNamespace(scalar_one=lambda: 1)
    assert next_order_no(DB(), SimpleNamespace(id=77)).endswith('-0001')
    assert any('ON DUPLICATE KEY UPDATE' in sql for sql in statements)


def test_knowledge_requires_version_and_rejects_lost_update(client, auth_headers):
    headers = auth_headers(1)
    source = client.get('/api/admin/inspection-items', headers=headers).json()['data']['list'][0]
    body = {key: source[key] for key in ('name','check_points','basis','defect_category','risk_level','applicable_types','applicable_phases')}
    item = client.post('/api/admin/inspection-items', headers=headers, json=body).json()['data']
    url = '/api/admin/inspection-items/' + str(item['id'])
    assert client.put(url, headers=headers, json=body).json()['code'] == 1000
    payload = {**body, 'expected_version': item['version']}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda i: client.put(url, headers=headers, json={**payload,'basis':f'并发依据{i}'}).json(), range(2)))
    assert sorted(r['code'] for r in results) == [0, 1014], results
    with SessionLocal() as db:
        current = db.get(InspectionItem, item['id'])
        assert current.version == item['version'] + 1
        revision = db.query(KnowledgeRevision).filter_by(item_id=item['id'], version=item['version']).one()
        assert revision.snapshot['basis'] == body['basis']
    assert client.delete(url, headers=headers).json()['code'] == 1000
    assert client.delete(url, headers=headers, params={'expected_version':item['version']}).json()['code'] == 1014


def test_plan_generation_is_bounded_and_preserves_backlog(client):
    today = now_utc().date()
    with SessionLocal() as db:
        item = db.query(InspectionItem).first()
        plan = InspectionPlan(project_id=1,item_id=item.id,inspector_id=2,name='bounded-backlog',
            start_date=today-timedelta(days=7),end_date=today,interval_days=1)
        db.add(plan); db.commit(); pid=plan.id
        assert generate_tasks(db, batch_size=3) == 3
        db.commit()
        assert db.query(PlanTask).filter_by(plan_id=pid).count() == 3
        assert generate_tasks(db, batch_size=3) == 3
        db.commit()
        assert generate_tasks(db, batch_size=3) == 2
        db.commit()
        assert generate_tasks(db, batch_size=3) == 0
        assert db.query(PlanTask).filter_by(plan_id=pid).count() == 8


def test_mysql_active_order_index_does_not_forbid_replacement_after_cancel():
    from sqlalchemy import create_mock_engine
    from app.models import RectifyOrder
    statements = []
    engine = create_mock_engine('mysql+pymysql://', lambda sql, *a, **k: statements.append(str(sql.compile(dialect=mysql.dialect()))))
    RectifyOrder.__table__.create(engine)
    ddl = '\n'.join(statements)
    assert "CASE WHEN status <> 'cancelled' THEN record_id ELSE NULL END" in ddl
    assert 'UNIQUE INDEX uq_order_active_record ON rectify_orders (record_id)' not in ddl

def test_concurrent_order_numbers_are_unique_in_real_database(client):
    from app.models import Project
    def allocate(_):
        with SessionLocal() as db:
            value = next_order_no(db, db.get(Project, 1))
            db.commit()
            return value
    with ThreadPoolExecutor(max_workers=4) as pool:
        numbers = list(pool.map(allocate, range(12)))
    assert len(numbers) == len(set(numbers)) == 12
    sequences = sorted(int(n.rsplit('-', 1)[1]) for n in numbers)
    assert sequences == list(range(sequences[0], sequences[0] + 12))

def test_active_order_constraint_allows_only_cancelled_replacements(client):
    import pytest
    from sqlalchemy.exc import IntegrityError
    from app.models import RectifyOrder
    with SessionLocal() as db:
        original = db.query(RectifyOrder).filter(RectifyOrder.status != 'cancelled').first()
        values = {column.name: getattr(original, column.name) for column in RectifyOrder.__table__.columns
                  if column.name not in ('id', 'active_record_id', 'order_no')}
        values['order_no'] = 'test-constraint-replacement'
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.add(RectifyOrder(**values)); db.flush()
        original.status = 'cancelled'; db.flush()
        db.add(RectifyOrder(**values)); db.flush()
        db.rollback()


def test_order_number_allocation_rolls_back_with_transaction(client):
    from app.models import Project
    with SessionLocal() as db:
        number = next_order_no(db, db.get(Project, 1))
        db.rollback()
        assert next_order_no(db, db.get(Project, 1)) == number
        db.rollback()

def test_metadata_compiles_for_both_production_databases():
    from sqlalchemy import create_mock_engine
    from sqlalchemy.dialects import postgresql
    from app.database import Base
    for url, dialect in [('mysql+pymysql://', mysql.dialect()), ('postgresql+psycopg://', postgresql.dialect())]:
        statements = []
        engine = create_mock_engine(url, lambda sql, *a, **k: statements.append(str(sql.compile(dialect=dialect))))
        Base.metadata.create_all(engine)
        assert len(statements) >= len(Base.metadata.tables)
