from datetime import date
import pytest
from sqlalchemy.dialects import mysql, postgresql, sqlite
from sqlalchemy.exc import UnsupportedCompilationError
from app.p1_models import KnowledgeRevision, AiBudget
from app.services.insert_once import insert_once


@pytest.mark.parametrize('table,values,keys',[
    (KnowledgeRevision.__table__,dict(item_id=1,version=1,snapshot={'name':'标准'}),['item_id','version']),
    (AiBudget.__table__,dict(key='project:1',day=date(2026,9,9),calls=0),['key','day']),
])
def test_mysql_reproduces_old_failure_and_compiles_fixed_insert(table,values,keys):
    old=sqlite.insert(table).values(**values).on_conflict_do_nothing(index_elements=keys)
    with pytest.raises(UnsupportedCompilationError): old.compile(dialect=mysql.dialect())
    sql=str(insert_once(table,values,keys,'mysql').compile(dialect=mysql.dialect()))
    assert 'ON DUPLICATE KEY UPDATE' in sql and 'ON CONFLICT' not in sql
    update=sql.split('ON DUPLICATE KEY UPDATE')[1]
    assert 'snapshot' not in update and 'calls' not in update
    for name,dialect in [('sqlite',sqlite.dialect()),('postgresql',postgresql.dialect())]:
        assert 'ON CONFLICT' in str(insert_once(table,values,keys,name).compile(dialect=dialect))


def test_existing_quota_not_reset(client):
    from app.database import SessionLocal
    with SessionLocal() as db:
        key='compatibility-test'
        values=dict(key=key,day=date(2026,9,9),calls=8)
        db.execute(insert_once(AiBudget.__table__,values,['key','day'],db.bind.dialect.name))
        db.execute(insert_once(AiBudget.__table__,dict(values,calls=0),['key','day'],db.bind.dialect.name))
        db.commit()
        assert db.get(AiBudget,(key,values['day'])).calls==8
