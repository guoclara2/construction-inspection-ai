# pytest 公共 fixture：独立临时库 + TestClient（生命周期内建表与种子）
import os
import tempfile
from pathlib import Path

# 必须在导入 app 前设置环境变量（conftest 先于测试模块加载）
_tmpdir = tempfile.mkdtemp(prefix="pytest_backend_")
_external_url = os.environ.get('INSPECTION_TEST_DATABASE_URL', '')
if _external_url:
    from sqlalchemy.engine import make_url
    _test_url = make_url(_external_url)
    if (os.environ.get('INSPECTION_TEST_EXTERNAL') != '1'
            or _test_url.get_backend_name() not in ('postgresql', 'mysql')
            or not (_test_url.database or '').startswith('inspection_test_')):
        raise RuntimeError('External tests require opt-in and a dedicated inspection_test_ database')
    # Preload the explicit DBAPI before SQLAlchemy builds its dialect. This avoids a
    # namespace-package collision from a host-level test runner on Windows.
    if _test_url.get_backend_name() == 'mysql':
        import importlib
        import sys
        if getattr(sys.modules.get('pymysql'), '__file__', True) is None:
            del sys.modules['pymysql']
            importlib.invalidate_caches()
        import pymysql
        if not hasattr(pymysql, 'paramstyle'):
            raise RuntimeError('PyMySQL driver is invalid; install the PyMySQL package')
os.environ["DATABASE_URL"] = _external_url or ("sqlite:///" + os.path.join(_tmpdir, "test.db").replace("\\", "/"))
os.environ["DASHSCOPE_API_KEY"] = ""  # 强制 mock 模式
os.environ["AI_PRODUCTION_ENABLED"] = "false"
os.environ["WX_APPID"] = ""
os.environ["WX_SECRET"] = ""
os.environ["SMS_PROVIDER"] = ""
os.environ["STORAGE_BACKEND"] = "local"
os.environ["APP_ENV"] = "dev"
os.environ["DEV_LOGIN_ENABLED"] = "true"   # 测试启用 dev-login 以便获取各角色令牌
# TestClient 的请求来源 IP 固定为 "testclient"，须加入 dev-login IP 白名单（P0-2）
os.environ["DEV_LOGIN_ALLOWED_IPS"] = "127.0.0.1,::1,testclient"
os.environ["REDIS_URL"] = ""                # 限流走进程内实现
os.environ["SCHEDULER_ENABLED"] = "false"   # 测试不启动定时任务（P06），调度逻辑由测试显式调用验证
os.environ["STORAGE_LOCAL_DIR"] = os.path.join(_tmpdir, "storage")  # 测试存储对象全部落临时目录
# 测试用种子账号密码（强密码；替代已废弃的 123456）。仅测试夹具使用。
SEED_PW = "Dev@Seed2026!"
os.environ["DEV_SEED_PASSWORD"] = SEED_PW

import pytest  # noqa: E402
import shutil  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, engine  # noqa: E402
from app import models  # noqa: E402,F401  注册模型
from app.main import app  # noqa: E402

# Every backend test run exercises actual migrations. Never erase a supplied database.
from sqlalchemy import inspect
from alembic import command
from alembic.config import Config
if inspect(engine).get_table_names():
    raise RuntimeError('Test database must be empty; refusing to modify an existing schema')
_migration_config = Config(str(Path(__file__).resolve().parents[2] / 'alembic.ini'))
command.upgrade(_migration_config, 'head')
command.check(_migration_config)


@pytest.fixture(scope="session")
def seed_password() -> str:
    return SEED_PW


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c
    shutil.rmtree(_tmpdir, ignore_errors=True)  # 清理临时库


def pytest_collection_modifyitems(items):
    if _external_url:
        for item in items:
            if item.name == 'test_backup_restore_integrity_and_path_guard':
                item.add_marker(pytest.mark.skip(reason='SQLite backup format; external DB recovery has separate acceptance'))


@pytest.fixture()
def png_bytes() -> bytes:
    """构造最小合法 PNG"""
    import struct
    import zlib

    def chunk(ctype, data):
        return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", zlib.crc32(ctype + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b""))


@pytest.fixture()
def auth_headers(client):
    """预置各角色请求头（经 dev-login 取 access_token）"""
    def _get(user_id: int) -> dict:
        r = client.post("/api/auth/dev-login", json={"user_id": user_id})
        assert r.json()["code"] == 0, r.text
        return {"Authorization": f"Bearer {r.json()['data']['access_token']}"}
    return _get
