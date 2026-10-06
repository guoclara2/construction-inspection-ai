import hashlib
import json
from datetime import datetime, timedelta, timezone
import pytest
from app.config import settings
from app.services import ai_acceptance as gate


def report(tmp_path):
    cases = [dict(id=str(i), image_sha256=hashlib.sha256(str(i).encode()).hexdigest(),
        mode='real', reviewer='independent-reviewer', label=i < 50,
        prediction='abnormal' if i < 50 else 'normal', severe=i < 25) for i in range(100)]
    data = dict(model='qwen3-vl-flash', approved_by='site-safety-owner',
        scope={'project_ids':[1], 'item_versions':[{'item_id':1,'version':1}], 'purposes':['analyze']},
        expires_at=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat(), cases=cases,
        pipeline_sha256=gate.pipeline_fingerprint(settings.AI_REVIEW_THRESHOLD))
    path=tmp_path/'acceptance.json'
    path.write_text(json.dumps(data),encoding='utf-8')
    return path,data


def test_current_scope_allowed_but_other_project_item_and_purpose_rejected(tmp_path,monkeypatch):
    path,data=report(tmp_path)
    monkeypatch.setattr(settings,'AI_EVALUATION_REPORT',str(path))
    gate.ensure_runtime_acceptance(1,'analyze',item_id=1,version=1)
    for args in [(2,'analyze',1,1),(1,'analyze',2,1),(1,'analyze',1,2),(1,'recommend',None,None)]:
        with pytest.raises(ValueError):
            gate.ensure_runtime_acceptance(args[0],args[1],item_id=args[2],version=args[3])


def test_report_expiry_rechecked_without_restart(tmp_path,monkeypatch):
    path,data=report(tmp_path)
    monkeypatch.setattr(settings,'AI_EVALUATION_REPORT',str(path))
    gate.ensure_runtime_acceptance(1,'analyze',item_id=1,version=1)
    data['expires_at']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
    path.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(ValueError,match='过期'):
        gate.ensure_runtime_acceptance(1,'analyze',item_id=1,version=1)


def test_threshold_change_invalidates_report(tmp_path,monkeypatch):
    path,_=report(tmp_path)
    monkeypatch.setattr(settings,'AI_REVIEW_THRESHOLD',.91)
    with pytest.raises(ValueError,match='变化'):
        gate.validate_report(path,review_threshold=settings.AI_REVIEW_THRESHOLD)


def test_scope_cannot_be_free_text_and_expiry_requires_timezone(tmp_path):
    path,data=report(tmp_path)
    data['scope']='all sites'
    path.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(ValueError): gate.validate_report(path)
    data['scope']={'project_ids':[1],'item_versions':[{'item_id':1,'version':1}],'purposes':['analyze']}
    data['expires_at']='2099-01-01T00:00:00'
    path.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(ValueError): gate.validate_report(path)


def test_gateway_does_not_call_vendor_or_reserve_quota_after_expiry(tmp_path,monkeypatch):
    from app.services.ai_client import ai_client,AIError
    from app.services.ai_meter import ai_scope
    path,data=report(tmp_path)
    data['expires_at']=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
    path.write_text(json.dumps(data),encoding='utf-8')
    monkeypatch.setattr(settings,'AI_EVALUATION_REPORT',str(path))
    monkeypatch.setattr(settings,'APP_ENV','prod')
    monkeypatch.setattr(settings,'AI_PRODUCTION_ENABLED',True)
    monkeypatch.setattr(ai_client,'_api_key','test-only')
    def forbidden(*a,**k):raise AssertionError('must reject before external access')
    monkeypatch.setattr(ai_client,'_get_client',forbidden)
    with ai_scope(1,2), pytest.raises(AIError,match='准入'):
        ai_client.chat([],purpose='analyze')


def test_notification_and_plan_tasks_have_single_periodic_entry():
    from app.services.scheduler import build_scheduler
    scheduler=build_scheduler()
    ids={j.id for j in scheduler.get_jobs()}
    assert 'notification_outbox' in ids and 'plan_recovery' in ids
    assert not ids.intersection({'retry_notifications','generate_plan_tasks','close_missed_plan_tasks'})
