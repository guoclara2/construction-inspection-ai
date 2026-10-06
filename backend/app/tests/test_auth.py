# 认证用例：dev-login / 登录 / 弱密码 / 锁定 / 令牌吊销 / refresh 重放 / 强制改密 / dev 路由生产隔离
import os
import subprocess
import sys


def test_dev_login(client):
    r = client.post("/api/auth/dev-login", json={"user_id": 2})
    body = r.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["access_token"] and data["refresh_token"]
    assert data["user"]["username"] == "liming"
    # 角色改由项目成员关系承载：默认项目内角色列表
    assert data["user"]["roles"] == ["inspector"]
    assert any(p["is_default"] for p in data["projects"])


def test_dev_login_invalid_user(client):
    r = client.post("/api/auth/dev-login", json={"user_id": 999})
    assert r.json()["code"] == 1001


def test_dev_login_ip_not_allowed(client, monkeypatch):
    """P0-2：来源 IP 不在 DEV_LOGIN_ALLOWED_IPS 白名单 → dev-login / dev-accounts 一律 404"""
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "DEV_LOGIN_ALLOWED_IPS", "10.10.10.10")
    r = client.post("/api/auth/dev-login", json={"user_id": 2})
    assert r.status_code == 404
    r = client.get("/api/auth/dev-accounts")
    assert r.status_code == 404


def test_dev_login_ip_wildcard_allowed(client, monkeypatch):
    """真机调试：白名单支持尾缀通配（192.168.1.* 放行局域网段）→ 命中网段放行，网段外仍 404"""
    from app.config import settings as app_settings
    monkeypatch.setattr(app_settings, "DEV_LOGIN_ALLOWED_IPS", "127.0.0.1,::1,192.168.1.*")
    # TestClient 来源 IP 固定为 testclient：不在网段内 → 404（通配不误放）
    r = client.post("/api/auth/dev-login", json={"user_id": 2})
    assert r.status_code == 404
    # 单元级验证通配匹配逻辑：网段内 IP 命中、网段外不命中、精确条目不受影响
    from app.routers.dev_auth import _ip_matches
    assert _ip_matches("192.168.1.23", "192.168.1.*")
    assert _ip_matches("192.168.1.254", "192.168.1.*")
    assert not _ip_matches("192.168.2.23", "192.168.1.*")
    assert not _ip_matches("192.168.10.23", "192.168.1.*")  # 前缀相同网段不同
    assert _ip_matches("127.0.0.1", "127.0.0.1")
    assert not _ip_matches("127.0.0.2", "127.0.0.1")


def test_password_login_ok(client, seed_password):
    r = client.post("/api/auth/login", json={"username": "admin", "password": seed_password})
    body = r.json()
    assert body["code"] == 0
    # admin 现为组织管理员（org_admin），项目内角色为 project_admin
    assert body["data"]["user"]["is_org_admin"] is True
    assert body["data"]["access_token"]


def test_password_login_wrong_password(client, seed_password):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "WrongPass!123"})
    assert r.json()["code"] == 1002
    r = client.post("/api/auth/login", json={"username": "nobody", "password": seed_password})
    assert r.json()["code"] == 1002


def test_me_requires_token(client):
    r = client.get("/api/auth/me")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == 401
    r = client.get("/api/auth/me", headers={"Authorization": "Bearer fake.token.here"})
    assert r.status_code == 401


def test_me_ok(client, auth_headers):
    r = client.get("/api/auth/me", headers=auth_headers(2))
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["name"] == "李明"


def test_admin_api_forbidden_for_inspector(client, auth_headers):
    # 越权现返回 HTTP 403（总纲 §5.0 / 2.5）
    r = client.get("/api/admin/users", headers=auth_headers(2))
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == 403
    r = client.get("/api/admin/users")
    assert r.status_code == 401


def test_admin_api_ok_for_admin(client, auth_headers):
    r = client.get("/api/admin/users", headers=auth_headers(1))
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["total"] >= 6


def test_weak_password_rejected(client, auth_headers, seed_password):
    # 用 wangfang(3) 真实旧密码校验强度分支
    r = client.post("/api/auth/password", headers=auth_headers(3),
                    json={"old_password": seed_password, "new_password": "123456"})
    body = r.json()
    assert body["code"] == 1000
    assert "强度" in body["msg"]


def test_token_version_revocation(client, seed_password):
    # wangfang 登录 → 改密 → 旧 access token 立即失效
    r = client.post("/api/auth/login", json={"username": "wangfang", "password": seed_password})
    data = r.json()["data"]
    old_token = data["access_token"]
    headers = {"Authorization": f"Bearer {old_token}"}
    assert client.get("/api/auth/me", headers=headers).json()["code"] == 0
    # 改密（强密码）
    new_pw = "Wf@NewPass2026"
    r2 = client.post("/api/auth/password", headers=headers,
                     json={"old_password": seed_password, "new_password": new_pw})
    assert r2.json()["code"] == 0
    # 旧 token 失效
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    # 新 token 可用
    new_token = r2.json()["data"]["access_token"]
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {new_token}"}).json()["code"] == 0
    # 还原密码，避免影响其他用例
    client.post("/api/auth/password", headers={"Authorization": f"Bearer {new_token}"},
                json={"old_password": new_pw, "new_password": seed_password})


def test_wx_login_cloud_channel(client, monkeypatch):
    """云托管同样验证 code；伪造头不能覆盖微信服务端返回身份。"""
    import app.auth as auth
    from app.config import settings
    from app.database import SessionLocal
    from app.models import User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == 2).first()
        user.wx_openid = "o-cloud-test-openid-0001"
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(settings, 'WX_APPID', 'test')
    monkeypatch.setattr(settings, 'WX_SECRET', 'test')
    monkeypatch.setattr(auth, '_wx_code2session', lambda code: {'openid': 'o-cloud-test-openid-0001'} if code == 'valid-code' else {})
    r = client.post("/api/auth/wx-login", json={"code": "valid-code"},
                    headers={"x-wx-source": "wx_client", "x-wx-openid": "forged-other-identity"})
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["user"]["id"] == 2

    # 缺少 code，即使存在身份头也必须拒绝。
    r2 = client.post("/api/auth/wx-login", json={"code": ""},
                     headers={"x-wx-openid": "o-cloud-test-openid-0001"})
    assert r2.json()["code"] == 4002


def test_login_lockout_after_5_failures(client, seed_password):
    # zhoutao(6)：前 4 次错误返回 1002（P1-1：msg 含剩余次数预警），第 5 次错误触发锁定 1061
    for i in range(4):
        r = client.post("/api/auth/login", json={"username": "zhoutao", "password": "Wrong!Pass99"})
        body = r.json()
        assert body["code"] == 1002
        # 第 i+1 次失败后剩余 4-i 次机会
        assert f"再错 {4 - i} 次将锁定 15 分钟" in body["msg"]
    r = client.post("/api/auth/login", json={"username": "zhoutao", "password": "Wrong!Pass99"})
    body = r.json()
    assert body["code"] == 1061  # 已锁定
    # 锁定后即便密码正确也拒绝
    r2 = client.post("/api/auth/login", json={"username": "zhoutao", "password": seed_password})
    assert r2.json().get("code") in (1061, 429) or r2.status_code == 429
    # 审计中应有 login_failed 与 login_locked
    from app.database import SessionLocal
    from app.models import AuditLog
    db = SessionLocal()
    try:
        actions = [a.action for a in db.query(AuditLog).all()]
        assert "login_failed" in actions
        assert "login_locked" in actions
    finally:
        db.close()


def test_refresh_rotation_and_reuse_detection(client, seed_password):
    # sunwei(5) 登录取 refresh；轮换一次成功；旧 refresh 再用 → 401 且账号令牌全失效
    r = client.post("/api/auth/login", json={"username": "sunwei", "password": seed_password})
    data = r.json()["data"]
    old_refresh = data["refresh_token"]
    r1 = client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
    assert r1.json()["code"] == 0
    new_refresh = r1.json()["data"]["refresh_token"]
    # 复用旧 refresh → 泄露检测
    r2 = client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
    assert r2.status_code == 401
    # 轮换出的新 refresh 也应因 token 全量吊销而失效
    r3 = client.post("/api/auth/refresh", json={"refresh_token": new_refresh})
    assert r3.status_code == 401
    from app.database import SessionLocal
    from app.models import AuditLog
    db = SessionLocal()
    try:
        assert any(a.action == "token_reuse_detected" for a in db.query(AuditLog).all())
    finally:
        db.close()


def test_must_change_password_interception(client, auth_headers):
    # 管理员创建用户 → 初始 must_change → 除自救路径外接口 1060 → 改密后放行
    r = client.post("/api/admin/users", headers=auth_headers(1),
                    json={"username": "newbie01", "name": "新人", "role": "inspector"})
    body = r.json()
    assert body["code"] == 0
    initial_pw = body["data"]["initial_password"]
    assert initial_pw and initial_pw != "123456"
    # 登录
    r2 = client.post("/api/auth/login", json={"username": "newbie01", "password": initial_pw})
    assert r2.json()["data"]["must_change_password"] is True
    token = r2.json()["data"]["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    # 非自救接口（带令牌）→ 1060
    assert client.get("/api/inspection-items", headers=h).json()["code"] == 1060
    # 自救接口 /me 放行
    assert client.get("/api/auth/me", headers=h).json()["code"] == 0
    # 改密
    r3 = client.post("/api/auth/password", headers=h,
                     json={"old_password": initial_pw, "new_password": "Newbie@2026x"})
    assert r3.json()["code"] == 0
    new_token = r3.json()["data"]["access_token"]
    # 改密后放行
    assert client.get("/api/inspection-items", headers={"Authorization": f"Bearer {new_token}"}).json()["code"] == 0


def test_dev_login_route_absent_in_prod():
    # 子进程以 prod 环境导入 app，断言 dev-login 路由不存在（返回 404 的根因）
    env = dict(os.environ)
    env.update({
        "APP_ENV": "prod",
        "JWT_SECRET": "x" * 40,
        "DATABASE_URL": "postgresql+psycopg://u:p@localhost:5432/none",
        "DASHSCOPE_API_KEY": "sk-test",
        "CORS_ORIGINS": "https://admin.example.com",
        "REDIS_URL": "redis://localhost:6379/0",
        "WX_APPID": "wx-test-app",
        "WX_SECRET": "wx-test-secret",
        "DEV_LOGIN_ENABLED": "false",
        "PYTHONUTF8": "1",
    })
    # 该 FastAPI 版本 include_router 采用惰性 _IncludedRouter，占位对象在 app 构建前不展开，
    # 因此需同时展开 original_router 的子路由再判断（否则平铺路径里看不到 /api/auth/login）。
    code = (
        "from app.main import app\n"
        "paths=set()\n"
        "for r in app.router.routes:\n"
        "    p=getattr(r,'path',None)\n"
        "    if p: paths.add(p)\n"
        "    orig=getattr(r,'original_router',None)\n"
        "    if orig is not None:\n"
        "        for s in orig.routes:\n"
        "            sp=getattr(s,'path',None)\n"
        "            if sp: paths.add(sp)\n"
        "assert '/api/auth/dev-login' not in paths, 'dev-login should NOT be registered in prod'\n"
        "assert '/api/auth/login' in paths, 'login route missing'\n"
        # P0-2：prod 环境下实际调用 dev-login 也必须 404（TestClient 不进 lifespan，不触库）
        "from fastapi.testclient import TestClient\n"
        "c = TestClient(app)\n"
        "r = c.post('/api/auth/dev-login', json={'user_id': 1})\n"
        "assert r.status_code == 404, f'dev-login should 404 in prod, got {r.status_code}'\n"
        "print('OK')\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, encoding="utf-8")
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "OK" in proc.stdout


def test_health_alias_route(client):
    """P3-1：/health 为 /api/health 的探活别名，同一 handler，返回结构一致"""
    r_alias = client.get("/health")
    assert r_alias.status_code == 200  # fixture applies and checks Alembic head before TestClient starts
    body = r_alias.json()
    assert body["code"] == 0
    assert body["data"]["status"] in ("ok", "degraded")
    assert set(body["data"]["checks"]) == {"database", "redis", "storage", "migration", "scheduler"}
    r_api = client.get("/api/health")
    assert r_api.status_code == 200
    assert r_api.json()["data"]["status"] == body["data"]["status"]
