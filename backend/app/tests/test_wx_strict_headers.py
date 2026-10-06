import uuid
import pytest
from sqlalchemy import event
from app.models import RefreshToken


@pytest.mark.parametrize('header_length', [0, 300, 301, 4096])
def test_wechat_long_client_header_fits_mysql_column(client, header_length, monkeypatch):
    import app.auth as auth
    from app.config import settings
    openid = 'strict-' + uuid.uuid4().hex
    monkeypatch.setattr(settings, 'WX_APPID', 'test')
    monkeypatch.setattr(settings, 'WX_SECRET', 'test')
    monkeypatch.setattr(auth, '_wx_code2session', lambda code: {'openid': openid} if code == 'valid' else {})
    def strict_varchar(mapper,connection,row):
        assert row.ua is None or len(row.ua)<=300, 'MySQL VARCHAR(300) overflow in refresh_tokens.ua'
    event.listen(RefreshToken,'before_insert',strict_varchar)
    try:
        result=client.post('/api/auth/wx-login',json={'code':'valid'},headers={
            'user-agent':'M'*header_length}).json()
        assert result['code']==0,result
        from app.database import SessionLocal
        with SessionLocal() as db:
            row=db.query(RefreshToken).filter_by(user_id=result['data']['user']['id']).one()
            assert row.ua == ('M'*min(header_length,300) or None)
        headers={'Authorization':'Bearer '+result['data']['access_token']}
        assert client.get('/api/inspection-items',headers=headers).json()['code']==0
    finally:
        event.remove(RefreshToken,'before_insert',strict_varchar)
