import uuid
from app.config import settings
from app.database import SessionLocal
from app.models import User, Project, Organization
from app.services.kv import set_kv


def test_first_wechat_login_private_project_and_repeat(client,monkeypatch):
    import app.auth as auth
    openid='wx-test-'+uuid.uuid4().hex
    monkeypatch.setattr(settings,'WX_APPID','test')
    monkeypatch.setattr(settings,'WX_SECRET','test')
    monkeypatch.setattr(auth,'_wx_code2session',lambda code:{'openid':openid})
    first=client.post('/api/auth/wx-login',json={'code':'valid'}).json()
    assert first['code']==0,first
    data=first['data'];assert data['must_change_password'] is False
    assert len(data['projects'])==1 and 'inspector' in data['user']['roles']
    second=client.post('/api/auth/wx-login',json={'code':'next'}).json()['data']
    assert second['user']['id']==data['user']['id']
    headers={'Authorization':'Bearer '+data['access_token']}
    items=client.get('/api/inspection-items',headers=headers).json()
    assert items['code']==0 and items['data']['list']
    foreign=client.get('/api/inspection-items',headers={**headers,'X-Project-Id':'1'}).json()
    assert foreign.get('code',foreign.get('detail',{}).get('code'))!=0
    with SessionLocal() as db:
        user=db.get(User,data['user']['id']);assert user.phone is None and user.org_id!=1
        user.active=False;db.commit()
    assert client.post('/api/auth/wx-login',json={'code':'third'}).json()['code']!=0


def test_phone_login_independent_and_single_use(client):
    phone='13900009991'
    with SessionLocal() as db:
        user=db.get(User,2);old=user.phone;openid=user.wx_openid
        user.phone=phone;db.commit()
    try:
        set_kv('smscode:login:'+phone,'123987',300)
        body={'phone':phone,'sms_code':'123987'}
        result=client.post('/api/auth/phone-login',json=body).json()
        assert result['code']==0 and result['data']['user']['id']==2,result
        assert client.post('/api/auth/phone-login',json=body).json()['code']!=0
        with SessionLocal() as db: assert db.get(User,2).wx_openid==openid
    finally:
        with SessionLocal() as db: db.get(User,2).phone=old;db.commit()


def test_unconfigured_sms_does_not_block_wechat(client,monkeypatch):
    import app.auth as auth
    from app.services.kv import get_kv
    monkeypatch.setattr(settings,'APP_ENV','prod')
    monkeypatch.setattr(auth.sms_channel,'send_sms',lambda *a:(False,'unconfigured'))
    response=client.post('/api/auth/sms-code',json={'phone':'13900009993','scene':'login'}).json()
    assert response['code']==4012
    assert get_kv('smscode:login:13900009993') is None
    monkeypatch.setattr(settings, 'WX_APPID', 'test')
    monkeypatch.setattr(settings, 'WX_SECRET', 'test')
    openid = 'isolated-' + uuid.uuid4().hex
    monkeypatch.setattr(auth, '_wx_code2session', lambda code: {'openid': openid} if code == 'valid' else {})
    response=client.post('/api/auth/wx-login',json={'code':'valid'}).json()
    assert response['code']==0 and response['data']['must_change_password'] is False


def test_linked_wechat_keeps_existing_user_and_project(client,monkeypatch):
    import app.auth as auth
    openid='linked-'+uuid.uuid4().hex
    monkeypatch.setattr(settings, 'WX_APPID', 'test')
    monkeypatch.setattr(settings, 'WX_SECRET', 'test')
    monkeypatch.setattr(auth, '_wx_code2session', lambda code: {'openid': openid} if code == 'valid' else {})
    with SessionLocal() as db:
        user=db.get(User,2);old=user.wx_openid;user.wx_openid=openid;db.commit()
        count=db.query(Organization).count()
    try:
        response=client.post('/api/auth/wx-login',json={'code':'valid'}).json()
        assert response['code']==0 and response['data']['user']['id']==2
        assert response['data']['user']['project_id']==1
        with SessionLocal() as db: assert db.query(Organization).count()==count
    finally:
        with SessionLocal() as db: db.get(User,2).wx_openid=old;db.commit()
