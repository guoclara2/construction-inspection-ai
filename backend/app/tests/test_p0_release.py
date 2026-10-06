import json
import pytest
from app.database import SessionLocal
from app.models import InspectionRecord, AiFeedback
from app.services import analyze as service
from app.services.ai_acceptance import evaluate, validate_report
from app.utils import BizError
from app.tests.test_attachments import _upload_ok, _create_record, _create_order, _jpeg_with_exif


def record(client, auth_headers):
    h = auth_headers(2)
    a = _upload_ok(client, h, _jpeg_with_exif())
    return h, a, _create_record(client, h, a['id'])


def test_assignee_original_photo_access_and_isolation(client, auth_headers):
    h = auth_headers(2)
    a = _upload_ok(client, h, _jpeg_with_exif())
    order = _create_order(client, h, a['id'], 4)
    r = client.get(f"/api/attachments/{a['id']}/signed-url", headers=auth_headers(4))
    assert r.status_code == 200
    assert client.get(r.json()['data']['url']).status_code == 200
    assert client.get(f"/api/attachments/{a['id']}/signed-url", headers=auth_headers(5)).status_code == 403
    assert client.get(f"/api/attachments/{a['id']}/signed-url", headers=auth_headers(7)).status_code == 403
    # Transferring removes the old assignee's access even for previously issued URLs.
    assert client.post(f"/api/orders/{order['id']}/transfer", headers=h, json={'assignee_id': 5, 'reason': '交接'}).json()['code'] == 0
    assert client.get(r.json()['data']['url']).status_code == 403


def test_abnormal_requires_complete_draft_then_can_retry(client, auth_headers):
    h, a, rid = record(client, auth_headers)
    url = f'/api/inspection-records/{rid}/confirm'
    assert client.post(url, headers=h, json={'human_verdict': 'abnormal'}).json()['code'] == 1013
    assert client.post(url, headers=h, json={'human_verdict': 'abnormal','order_draft': {'assignee_id':4}}).json()['code'] == 1013
    preview = client.get(f'/api/inspection-records/{rid}/draft', headers=h).json()['data']
    draft = dict(assignee_id=4, problem_location=' 东侧第二支撑裂缝 ', rectify_requirement='人工核定措施，现场复核', basis='人工确认依据', sla_hours=preview['sla_hours'])
    response = client.post(url, headers=h, json={'human_verdict':'abnormal','order_draft':draft}).json()
    assert response['code'] == 0
    for key in ('problem_location','rectify_requirement','basis'):
        assert response['data']['order'][key] == draft[key]
    assert response['data']['record']['ai_mode'] == 'manual'
    assert client.post(f'/api/inspection-records/{rid}/analyze', headers=h).json()['code'] == 1012


def test_normal_manual_confirmation_and_late_analysis_cannot_overwrite(client, auth_headers):
    h, a, rid = record(client, auth_headers)
    assert client.post(f'/api/inspection-records/{rid}/confirm', headers=h, json={'human_verdict':'normal'}).json()['code'] == 0
    with SessionLocal() as db:
        before = db.get(InspectionRecord, rid).ai_result
        with pytest.raises(BizError):
            service._save_analysis(rid, {'has_defect': True}, 'real', db)
        assert db.get(InspectionRecord, rid).ai_result == before
        assert db.query(AiFeedback).filter_by(record_id=rid).count() == 0


def test_missing_original_never_calls_model(client, auth_headers, monkeypatch):
    h, a, rid = record(client, auth_headers)
    monkeypatch.setattr(service.ai_client, '_api_key', 'test-only')
    monkeypatch.setattr(service, '_load_photo_base64', lambda *a: None)
    def prohibited(*args, **kwargs):
        raise AssertionError('Missing photograph must never call the model')
    monkeypatch.setattr(service.ai_client, 'chat', prohibited)
    response = client.post(f'/api/inspection-records/{rid}/analyze', headers=h).json()['data']
    assert response['ai_result']['has_defect'] is None
    assert response['actionable'] is False
    assert response['ai_result']['status'] == 'unknown'


def test_low_confidence_result_is_unknown_and_saved_once(client, auth_headers, monkeypatch):
    h, a, rid = record(client, auth_headers)
    monkeypatch.setattr(service.ai_client, '_api_key', 'test-only')
    calls = []
    def respond(*args, **kwargs):
        calls.append(True)
        return {'content': json.dumps(dict(has_defect=False, confidence=.2, severity='无', defect_desc='看不清', basis='', suggestion='复核'))}
    monkeypatch.setattr(service.ai_client, 'chat', respond)
    url = f'/api/inspection-records/{rid}/analyze'
    result = client.post(url, headers=h).json()['data']
    assert result['ai_result']['has_defect'] is None
    assert client.post(url, headers=h).json()['data'] == result
    assert len(calls) == 1


def test_sla_change_requires_confirmation(client, auth_headers):
    h, a, rid = record(client, auth_headers)
    draft = dict(assignee_id=4, problem_location='位置', rectify_requirement='整改',basis='依据',sla_hours=99999)
    assert client.post(f'/api/inspection-records/{rid}/confirm',headers=h,json={'human_verdict':'abnormal','order_draft':draft}).json()['code'] == 1014
    with SessionLocal() as db:
        assert db.get(InspectionRecord,rid).human_verdict is None


def test_ai_metrics_count_unknown_as_missed_positive():
    cases = [dict(id=str(i), image_sha256=f'{i:064x}',mode='real',reviewer='专业复核',label=True,severe=True,prediction=p) for i,p in enumerate(['unknown','abnormal'])]
    result = evaluate(cases)
    assert result['recall'] == .5
    assert result['severe_recall'] == .5
    assert result['unknown_rate'] == .5
    cases[0]['mode']='mock'
    with pytest.raises(ValueError): evaluate(cases)
    with pytest.raises(ValueError): validate_report('')


def test_manual_production_gate_never_calls_model(client, auth_headers, monkeypatch):
    h, a, rid = record(client, auth_headers)
    monkeypatch.setattr(service.settings, 'APP_ENV', 'prod')
    monkeypatch.setattr(service.settings, 'AI_PRODUCTION_ENABLED', False)
    result = client.post(f'/api/inspection-records/{rid}/analyze', headers=h).json()['data']
    assert result['mode'] == 'manual'
    assert result['ai_result']['has_defect'] is None
