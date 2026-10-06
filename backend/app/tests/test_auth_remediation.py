"""Security regression contracts; external providers are never contacted."""
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy.orm import Session

import app.auth as auth
from app.config import settings
from app.database import SessionLocal
from app.models import Project, RefreshToken, User
from app.services.kv import set_kv


@pytest.fixture
def account(client):
    with SessionLocal() as db:
        user = User(username='auth-' + uuid.uuid4().hex, name='Auth regression',
                    password_hash=auth.hash_password('Auth@Test2026!'), org_id=1,
                    phone='139' + str(uuid.uuid4().int % 100000000).zfill(8),
                    wx_openid='auth-' + uuid.uuid4().hex, must_change_password=False)
        db.add(user)
        db.commit()
        return {'id': user.id, 'username': user.username, 'openid': user.wx_openid, 'phone': user.phone}


@pytest.mark.parametrize('cloud', [False, True])
def test_forged_identity_headers_never_authenticate(client, account, monkeypatch, cloud):
    monkeypatch.setattr(settings, 'APP_ENV', 'prod')
    monkeypatch.setattr(settings, 'WX_CLOUD_CALL', cloud)
    result = client.post('/api/auth/wx-login', json={}, headers={
        'x-wx-source': 'wx_client', 'x-wx-openid': account['openid']}).json()
    assert result['code'] != 0
    assert result['data'] is None


@pytest.mark.parametrize('method', ['password', 'dev', 'wx', 'phone'])
def test_each_login_persists_rotatable_refresh(client, account, monkeypatch, method):
    if method == 'password':
        path, body = 'login', {'username': account['username'], 'password': 'Auth@Test2026!'}
    elif method == 'dev':
        path, body = 'dev-login', {'user_id': account['id']}
    elif method == 'wx':
        monkeypatch.setattr(settings, 'WX_APPID', 'test-app')
        monkeypatch.setattr(settings, 'WX_SECRET', 'test-secret')
        monkeypatch.setattr(auth, '_wx_code2session', lambda code: {'openid': account['openid']} if code == 'verified' else {})
        path, body = 'wx-login', {'code': 'verified'}
    else:
        set_kv('smscode:login:' + account['phone'], '123456', 300)
        path, body = 'phone-login', {'phone': account['phone'], 'sms_code': '123456'}
    login = client.post('/api/auth/' + path, json=body).json()['data']
    result = client.post('/api/auth/refresh', json={'refresh_token': login['refresh_token']})
    assert result.status_code == 200, result.text
    next_token = result.json()['data']['refresh_token']
    assert next_token != login['refresh_token']
    assert client.post('/api/auth/refresh', json={'refresh_token': login['refresh_token']}).status_code == 401
    assert client.post('/api/auth/refresh', json={'refresh_token': next_token}).status_code == 401


def test_me_rejects_project_outside_authorized_set(client, account):
    with SessionLocal() as db:
        p = Project(org_id=1, name='PRIVATE PROJECT', code=uuid.uuid4().hex, project_type='building', phase='foundation')
        db.add(p)
        db.commit()
        pid = p.id
    login = client.post('/api/auth/dev-login', json={'user_id': account['id']}).json()['data']
    response = client.get('/api/auth/me', headers={'Authorization': 'Bearer ' + login['access_token'], 'X-Project-Id': str(pid)})
    assert response.status_code == 403
    assert 'PRIVATE PROJECT' not in response.text


def test_sms_provider_template_receives_six_digits(client, account, monkeypatch):
    for key, value in {'SMS_PROVIDER': 'aliyun', 'SMS_AK': 'fake', 'SMS_SK': 'fake', 'SMS_SIGN': 'test', 'SMS_TEMPLATE_CODE': 'test'}.items():
        monkeypatch.setattr(settings, key, value)
    captured = []
    def send(url, **kwargs):
        captured.append(json.loads(kwargs['params']['TemplateParam']))
        return type('Response', (), {'json': lambda self: {'Code': 'OK'}})()
    monkeypatch.setattr(auth.sms_channel.httpx, 'get', send)
    assert client.post('/api/auth/sms-code', json={'phone': account['phone']}).json()['code'] == 0
    code = captured[0]['code']
    assert len(code) == 6 and code.isascii() and code.isdigit()


def test_concurrent_refresh_has_exactly_one_winner(client, account, monkeypatch):
    login = client.post('/api/auth/login', json={'username': account['username'], 'password': 'Auth@Test2026!'}).json()['data']
    barrier = Barrier(2)
    original = Session.get
    def simultaneous_user_read(db, entity, ident, *args, **kwargs):
        result = original(db, entity, ident, *args, **kwargs)
        if entity is User and ident == account['id']:
            barrier.wait(timeout=15)
        return result
    with monkeypatch.context() as patch:
        patch.setattr(Session, 'get', simultaneous_user_read)
        def call(_):
            return client.post('/api/auth/refresh', json={'refresh_token': login['refresh_token']})
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(call, range(2)))
    assert sorted(r.status_code for r in responses) == [200, 401]
    winner = next(r.json()['data'] for r in responses if r.status_code == 200)
    assert client.post('/api/auth/refresh', json={'refresh_token': winner['refresh_token']}).status_code == 200


def test_retired_bind_tickets_cannot_rebind_account(client, account):
    ticket = uuid.uuid4().hex
    set_kv('wxbind:' + ticket, 'attacker-openid|', 600)
    set_kv('smscode:login:' + account['phone'], '123456', 300)
    response = client.post('/api/auth/wx-bind', json={'bind_ticket': ticket, 'phone': account['phone'], 'sms_code': '123456'})
    assert response.json()['code'] != 0
    with SessionLocal() as db:
        assert db.get(User, account['id']).wx_openid == account['openid']


def test_refresh_issuance_failure_rolls_back_claim_and_replacement(client, account, monkeypatch):
    login = client.post('/api/auth/dev-login', json={'user_id': account['id']}).json()['data']
    original = auth.issue_refresh_token
    def fail_after_insert(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('simulated persistence failure')
    with monkeypatch.context() as patch:
        patch.setattr(auth, 'issue_refresh_token', fail_after_insert)
        with pytest.raises(RuntimeError, match='simulated persistence failure'):
            client.post('/api/auth/refresh', json={'refresh_token': login['refresh_token']})
    with SessionLocal() as db:
        tokens = db.query(RefreshToken).filter_by(user_id=account['id']).all()
        assert len(tokens) == 1
        assert tokens[0].revoked is False and tokens[0].replaced_by is None
    assert client.post('/api/auth/refresh', json={'refresh_token': login['refresh_token']}).status_code == 200


def test_invalid_wechat_code_cannot_fall_back_to_forged_headers(client, account, monkeypatch):
    monkeypatch.setattr(settings, 'WX_APPID', 'test')
    monkeypatch.setattr(settings, 'WX_SECRET', 'test')
    monkeypatch.setattr(auth, '_wx_code2session', lambda code: {'errcode': 40029})
    result = client.post('/api/auth/wx-login', json={'code': 'invalid'}, headers={
        'x-wx-source': 'wx_client', 'x-wx-openid': account['openid']}).json()
    assert result['code'] == 4002 and result['data'] is None
