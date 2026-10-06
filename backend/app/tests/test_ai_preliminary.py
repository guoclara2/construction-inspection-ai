import json
from types import SimpleNamespace
from app.tests.test_p1_operations import flow
from app.database import SessionLocal
from app.models import InspectionRecord
from app.p1_models import AiJob
from app.config import settings
from app.services.analyze import prepare_preliminary, is_actionable_ai_result, analyze_record, _history_summaries
from app.services.ai_client import ai_client
from app.services.operations import process_ai_jobs
from app.services.ai_feedback_dataset import feedback_pairs


def output(basis='模型改写的条款', confidence=.8):
    return {'has_defect': True, 'defect_desc': '照片右侧疑似缺少防护栏杆',
            'severity': '严重', 'basis': basis, 'confidence': confidence, 'suggestion': '现场核验并补齐防护'}


def test_unverified_citation_preserves_observation_without_prefill():
    result=prepare_preliminary(output(),SimpleNamespace(item_basis='冻结的标准'))
    assert result['preliminary']['has_defect'] is True
    assert result['defect_desc']=='照片右侧疑似缺少防护栏杆'
    assert result['basis']=='' and result['has_defect'] is None
    assert not is_actionable_ai_result(result)
    verified=prepare_preliminary(output('冻结的标准'),SimpleNamespace(item_basis='冻结的标准'))
    assert is_actionable_ai_result(verified)


def test_legacy_unknown_retry_and_feedback_pair(flow,monkeypatch):
    f=flow; r,_=f.record(); rid=r['id']
    old={'has_defect':None,'confidence':0,'status':'unknown','defect_desc':'模型引用的依据无法在冻结标准中核验，请人工复核'}
    with SessionLocal() as db:
        rec=db.get(InspectionRecord,rid);rec.ai_result=json.dumps(old);rec.ai_mode='real'
        db.add(AiJob(record_id=rid,status='done',attempts=1));db.commit()
    monkeypatch.setattr(settings,'SCHEDULER_ENABLED',True)
    monkeypatch.setattr(ai_client,'_api_key','test-real')
    monkeypatch.setattr(ai_client,'chat',lambda *a,**kw: {'content':json.dumps(output()),'mode':'real'})
    url=f'/api/inspection-records/{rid}'
    assert f.c.post(url+'/analysis-job',headers=f.h).json()['data']['status']=='pending'
    process_ai_jobs()
    result=f.c.get(url+'/analysis-job',headers=f.h).json()['data']
    assert result['status']=='done'
    ai=result['record']['ai_result']
    assert ai['preliminary']['has_defect'] is True and ai['attempt_history']==[old]
    assert f.c.post(url+'/confirm',headers=f.h,json={'human_verdict':'normal','note':'右侧栏杆实际完整，遮挡造成误判'}).json()['code']==0
    assert f.c.post(url+'/analysis-job',headers=f.h).json()['code']!=0
    with SessionLocal() as db:
        pair=next(p for p in feedback_pairs(db,1) if p['record_id']==rid)
        assert pair['disagreement'] is True and pair['eligible_for_training'] is False
        assert not any(p['record_id']==rid for p in feedback_pairs(db,2))
        assert db.get(InspectionRecord,rid).ai_result


def test_history_uses_human_correction_not_wrong_ai(flow):
    f=flow; previous,_=f.record(); current,_=f.record()
    with SessionLocal() as db:
        row=db.get(InspectionRecord,previous['id']);row.human_verdict='normal'
        row.note='人工检查栏杆完整';row.ai_result=json.dumps(output());db.commit()
        history=_history_summaries(db,db.get(InspectionRecord,current['id']))
        assert '人工检查栏杆完整' in history and '照片右侧疑似缺少防护栏杆' not in history
