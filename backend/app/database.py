# 数据库连接：SQLAlchemy 2.0 风格 engine + Session + Base
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool

from .config import settings

if settings.db_url.startswith("sqlite"):
    # 本地快速调试：单文件 SQLite。
    # NullPool：连接直连直关不排队——QueuePool(5+10) 在并发压测下会排队超时；
    # WAL + busy_timeout：读写并行、写事务排队等锁而非报 locked（P05 验证门 100 并发建单）。
    engine = create_engine(
        settings.db_url,
        connect_args={"check_same_thread": False, "timeout": 30},
        poolclass=NullPool,
    )

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()
else:
    # 生产：PostgreSQL 连接池（预连接探活 + 定期回收，避免连接失效）
    engine = create_engine(
        settings.db_url,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        pool_recycle=1800,
    )

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
