from datetime import timedelta
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
import uuid
import pytest
from app.config import settings
from app.database import SessionLocal
from app.models import Attachment, InspectionItem, InspectionRecord, Project, ProjectMember, RectifyOrder, Notification
from app.p1_models import AiJob, AiBudget, AiUsage, PlanTask, KnowledgeRevision
from app.services.operations import process_ai_jobs, plan_tick, freeze_item
from app.services.storage import get_storage
from app.utils import now_utc, BizError

@pytest.fixture
def flow(client,auth_headers,png_bytes):
    h=auth_headers(2)
    item=client.get('/api/inspection-items?keyword=支护',headers=h).json()['data']['list'][0]
    def photo(user=2,biz='record'):
        key=uuid.uuid4().hex+'.png';get_storage().put(key,png_bytes,'image/png')
        with SessionLocal() as db:
            a=Attachment(org_id=1,project_id=1,uploader_id=user,biz_type=biz,file_key=key,raw_key=key,
                file_name=key,mime='image/png',size=len(png_bytes),media_type='image',source='camera',sha256=uuid.uuid4().hex)
            db.add(a);db.commit();return a.id
    def record(**extras):
        body={'item_id':item['id'],'attachment_id':photo(),**extras}
        r=client.post('/api/inspection-records',json=body,headers=h).json()
        assert r['code']==0,r
        return r['data'],body
    def order():
        r,_=record()
        body={'human_verdict':'abnormal','order_draft':{'assignee_id':4,'problem_location':'A区轴线1',
            'rectify_requirement':'加固并现场复核','basis':item['basis']}}
        res=client.post(f"/api/inspection-records/{r['id']}/confirm",json=body,headers=h).json()
        assert res['code']==0,res
        return res['data']['order']
    return SimpleNamespace(c=client,h=h,headers=auth_headers,item=item,photo=photo,record=record,order=order)

def test_durable_creation_confirmation_and_body_conflict(flow):
    f=flow;rec,body=f.record(request_key=uuid.uuid4().hex,location={'building':'A栋','measurement':'裂缝宽度0.4毫米'})
    repeat=f.c.post('/api/inspection-records',json=body,headers=f.h).json()
    assert repeat['data']['id']==rec['id']
    assert f.c.post('/api/inspection-records',json={**body,'note':'不同内容'},headers=f.h).json()['code']==1014
    headers={**f.h,'Idempotency-Key':uuid.uuid4().hex}
    payload={'human_verdict':'normal','note':'现场核验'}
    first=f.c.post(f"/api/inspection-records/{rec['id']}/confirm",json=payload,headers=headers).json()
    repeat=f.c.post(f"/api/inspection-records/{rec['id']}/confirm",json=payload,headers=headers).json()
    assert first['code']==repeat['code']==0
    assert repeat['data']['record']['location']['building']=='A栋'
    bad=f.c.post(f"/api/inspection-records/{rec['id']}/confirm",json=payload,headers={**headers,'X-Project-Id':'2'}).json()
    assert bad.get('code',bad.get('detail',{}).get('code'))!=0

def test_parallel_creation_same_request_one_record(flow):
    f=flow;body={'item_id':f.item['id'],'attachment_id':f.photo(),'request_key':uuid.uuid4().hex}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:f.c.post('/api/inspection-records',json=body,headers=f.h).json(),range(2)))
    with SessionLocal() as db: assert db.query(InspectionRecord).filter_by(request_key=body['request_key']).count()==1
    assert any(r['code']==0 for r in results)
    recovered=f.c.post('/api/inspection-records',json=body,headers=f.h).json()
    assert recovered['code']==0

def test_async_job_recovery_and_manual_freeze(flow,monkeypatch):
    f=flow;r,_=f.record();monkeypatch.setattr(settings,'SCHEDULER_ENABLED',True)
    a=f.c.post(f"/api/inspection-records/{r['id']}/analysis-job",headers=f.h).json()
    b=f.c.post(f"/api/inspection-records/{r['id']}/analysis-job",headers=f.h).json()
    assert a['data']['id']==b['data']['id']
    with SessionLocal() as db:
        job=db.get(AiJob,a['data']['id']);job.status='running';job.lease_until=now_utc()-timedelta(minutes=1);db.commit()
    assert process_ai_jobs()==1
    result=f.c.get(f"/api/inspection-records/{r['id']}/analysis-job",headers=f.h).json()['data']
    assert result['status']=='done' and result['record']['ai_result']
    r2,_=f.record();f.c.post(f"/api/inspection-records/{r2['id']}/analysis-job",headers=f.h)
    f.c.post(f"/api/inspection-records/{r2['id']}/confirm",json={'human_verdict':'normal'},headers=f.h)
    process_ai_jobs()
    with SessionLocal() as db: assert db.query(AiJob).filter_by(record_id=r2['id']).one().status=='cancelled'

def test_full_rounds_and_reviewer_pool_notifications(flow):
    f=flow;o=f.order();url=f"/api/orders/{o['id']}";h=f.headers(4)
    assert f.c.post(url+'/accept',headers=h).json()['code']==0
    assert f.c.post(url+'/start',headers=h).json()['code']==0
    text='现场加固说明'*300
    for number in (1,2):
        result=f.c.post(url+'/feedback',headers=h,json={'attachment_id':f.photo(4,'order_feedback'),
            'attachment_ids':[f.photo(4,'order_feedback')],'text':text+str(number)}).json()
        assert result['code']==0,result
        reviewh=f.headers(9)
        pool=f.c.get('/api/orders?view=review',headers=reviewh).json()['data']['list']
        assert o['id'] in [r['id'] for r in pool]
        result=f.c.post(url+'/review',headers=reviewh,json={'result':'reject' if number==1 else 'pass','comment':'复核意见'}).json()
        assert result['code']==0,result
    detail=f.c.get(url,headers=f.h).json()['data']
    assert len(detail['rounds'])==2 and detail['rounds'][0]['text']==text+'1'
    assert all(len(r['photos'])==2 for r in detail['rounds'])
    with SessionLocal() as db:
        assert db.query(Notification).filter_by(biz_id=o['id'],template_code='ORDER_FEEDBACK',channel='inapp').count()==2

def test_no_self_review_with_dual_role(flow):
    f=flow;o=f.order();url=f"/api/orders/{o['id']}";h=f.headers(4)
    f.c.post(url+'/accept',headers=h);f.c.post(url+'/start',headers=h)
    f.c.post(url+'/feedback',headers=h,json={'attachment_id':f.photo(4,'order_feedback'),'text':'完成'})
    with SessionLocal() as db:
        m=ProjectMember(project_id=1,user_id=4,role='reviewer',active=True);db.add(m);db.commit();mid=m.id
    try:
        assert f.c.post(url+'/review',headers=f.headers(4),json={'result':'pass'}).json()['code']!=0
    finally:
        with SessionLocal() as db:db.delete(db.get(ProjectMember,mid));db.commit()

def test_extension_approval_version_and_audit(flow):
    f=flow;o=f.order();url=f"/api/orders/{o['id']}"
    r=f.c.post(url+'/extensions',headers=f.headers(4),json={'deadline':(now_utc()+timedelta(days=30)).isoformat(),
        'reason':'材料到场延期，期间已隔离警戒','expected_version':o['version']}).json()
    assert r['code']==0,r
    eid=r['data']['id'];current=f.c.get(url,headers=f.h).json()['data']
    result=f.c.post(url+f'/extensions/{eid}/review',headers=f.h,json={'approve':True,'comment':'同意，维持隔离',
        'expected_version':current['version']}).json()
    assert result['code']==0
    assert f.c.get(url+'/extensions',headers=f.h).json()['data'][0]['status']=='approved'
    repeat=f.c.post(url+f'/extensions/{eid}/review',headers=f.h,json={'approve':True,'comment':'重复',
        'expected_version':current['version']}).json()
    assert repeat['code']!=0

def test_plan_generation_missed_completion_and_scope(flow):
    f=flow;today=now_utc().astimezone(__import__('zoneinfo').ZoneInfo(settings.APP_TIMEZONE)).date()
    r=f.c.post('/api/plans',headers=f.headers(1),json={'name':'每日支护','item_id':f.item['id'],'inspector_id':2,
        'start_date':str(today-timedelta(days=1)),'end_date':str(today),'interval_days':1}).json()
    assert r['code']==0,r
    pid=r['data']['id'];plan_tick();plan_tick()
    with SessionLocal() as db:
        tasks=db.query(PlanTask).filter_by(plan_id=pid).order_by(PlanTask.id).all()
        assert len(tasks)==2 and tasks[0].status=='missed';tid=tasks[0].id
    rec,_=f.record(task_id=tid)
    f.c.post(f"/api/inspection-records/{rec['id']}/confirm",json={'human_verdict':'normal'},headers=f.h)
    with SessionLocal() as db: assert db.get(PlanTask,tid).status=='late'
    rows=f.c.get('/api/plan-tasks',headers=f.headers(7)).json()['data']['list']
    assert tid not in [r['id'] for r in rows]

def test_snapshot_and_publication_separation(flow):
    f=flow;admin=f.headers(1)
    body={k:f.item[k] for k in ('name','check_points','basis','defect_category','risk_level','applicable_types','applicable_phases')}
    item=f.c.post('/api/admin/inspection-items',json=body,headers=admin).json()['data']
    assert item['publication']=='draft' and not item['enabled']
    publish={'expected_version':item['version'],'effective_from':(now_utc()-timedelta(hours=1)).isoformat(),
        'effective_until':(now_utc()+timedelta(days=30)).isoformat()}
    assert f.c.post(f"/api/knowledge/{item['id']}/publish",json=publish,headers=admin).json().get('code')!=0
    with SessionLocal() as db:
        m=ProjectMember(project_id=1,user_id=9,role='project_admin',active=True);db.add(m);db.commit();mid=m.id
    try:
        result=f.c.post(f"/api/knowledge/{item['id']}/publish",json=publish,headers=f.headers(9)).json();assert result['code']==0,result
        rec,_=f.record(item_id=item['id'])
        old=rec['item_snapshot']
        f.c.put(f"/api/admin/inspection-items/{item['id']}",json={**body,'basis':'修订后的依据','expected_version':result['data']['version']},headers=admin)
        fresh=f.c.get(f"/api/inspection-records/{rec['id']}",headers=f.h).json()['data']
        assert fresh['item_snapshot']==old
        history=f.c.get(f"/api/knowledge/{item['id']}/versions",headers=admin).json()['data']
        assert len(history)>=3
    finally:
        with SessionLocal() as db:db.delete(db.get(ProjectMember,mid));db.commit()

def test_ai_quota_usage_and_concurrency(monkeypatch,client):
    from app.services.ai_meter import ai_scope,reserve,finish
    monkeypatch.setattr(settings,'AI_DAILY_QUOTA',2);monkeypatch.setattr(settings,'AI_USER_DAILY_QUOTA',2)
    monkeypatch.setattr(settings,'AI_PROJECT_CONCURRENCY',1)
    with SessionLocal() as db:
        db.query(AiBudget).delete();db.query(AiUsage).delete();db.commit()
    with ai_scope(1,2):
        first=reserve('analyze','test-model')
        with pytest.raises(BizError):reserve('analyze','test-model')
        finish(first,SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10,completion_tokens=20)))
        second=reserve('analyze','test-model');finish(second)
        with pytest.raises(BizError):reserve('analyze','test-model')
    with SessionLocal() as db:
        assert db.get(AiUsage,first).input_tokens==10
        assert db.get(AiBudget,('project:1',now_utc().date())).calls==2

def test_notification_claim_recovery_once(client,monkeypatch):
    from app.services import notify as module
    with SessionLocal() as db:
        n=Notification(user_id=2,org_id=1,project_id=1,title='恢复',content='测试',channel='inapp',status='sending',
            lease_until=now_utc()-timedelta(seconds=1),dedup_key=uuid.uuid4().hex)
        db.add(n);db.commit();nid=n.id
    sent=[]
    original=module._send_one
    def send(db,n):
        if n.id==nid:sent.append(n.id)
        original(db,n)
    monkeypatch.setattr(module,'_send_one',send)
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda _:module.notification_tick(),range(2)))
    assert sent==[nid]
    with SessionLocal() as db:assert db.get(Notification,nid).status=='sent'

def test_cross_org_same_code_order_numbers(client):
    from app.services.order_flow import next_order_no
    from app.models import Organization
    with SessionLocal() as db:
        org=Organization(name='独立测试组织',code=uuid.uuid4().hex);db.add(org);db.flush()
        existing=db.get(Project,1)
        project=Project(org_id=org.id,name='同代码项目',code=existing.code,project_type='building',phase='foundation')
        db.add(project);db.flush()
        assert next_order_no(db,project)!=next_order_no(db,existing)
        db.rollback()

def test_multi_photo_foreign_attachment_rejected(flow):
    f=flow;other=f.photo(4)
    r=f.c.post('/api/inspection-records',headers=f.h,json={'item_id':f.item['id'],'attachment_id':f.photo(),
        'attachment_ids':[other]}).json()
    assert r.get('code',r.get('detail',{}).get('code'))!=0

def test_evidence_metadata_persists_and_upload_replay(flow,png_bytes):
    f=flow;key=uuid.uuid4().hex;data={'biz_type':'record','source':'camera','upload_key':key,
        'shot_at':'2026-01-01T12:00:00+08:00','client_lat':'30','client_lng':'120','accuracy':'50','coordinate_system':'gcj02'}
    r=f.c.post('/api/attachments',headers=f.h,data=data,files={'file':('evidence.png',png_bytes,'image/png')}).json()
    assert r['code']==0,r
    repeat=f.c.post('/api/attachments',headers=f.h,data=data,files={'file':('evidence.png',png_bytes,'image/png')}).json()
    assert repeat['data']['id']==r['data']['id']
    att=repeat['data'];assert att['evidence']['coordinate_system']=='gcj02' and att['gps_accuracy']==50
    assert '拍摄时间与上传时间相差过大' in att['evidence']['reasons']
    reviewed=f.c.post(f"/api/attachments/{att['id']}/evidence-review",headers=f.h,json={'comment':'已现场核对位置，照片时间异常待调查'}).json()
    assert reviewed['data']['suspicious'] and len(reviewed['data']['evidence']['reviews'])==1

def test_backup_restore_integrity_and_path_guard(client,tmp_path,png_bytes):
    from scripts.backup_restore import backup,restore,verify,inside
    # Earlier legacy-data tests intentionally insert missing objects. Restore only
    # those test fixtures here; production backup correctly fails on missing evidence.
    with SessionLocal() as db:
        for a in db.query(Attachment).filter_by(deleted_at=None):
            for key in {a.raw_key,a.file_key}:
                if key and not get_storage().exists(key):get_storage().put(key,png_bytes,'image/png')
    src=tmp_path/'backup';dst=tmp_path/'restore'
    assert backup(src)['verified'];assert restore(src,dst)['verified'];assert verify(dst)
    with pytest.raises(ValueError):restore(src,dst)
    with pytest.raises(ValueError):inside(dst,'../outside')
    (dst/'app.db').write_bytes(b'corrupt')
    with pytest.raises(ValueError):verify(dst)


def test_handoff_and_disable_guard(flow):
    f=flow;o=f.order();admin=f.headers(1)
    from app.models import User
    with SessionLocal() as db:
        ids=[]
        for _ in range(2):
            u=User(org_id=1,username=uuid.uuid4().hex,name='交接测试成员',password_hash=db.get(User,4).password_hash)
            db.add(u);db.flush();ids.append(u.id)
            db.add(ProjectMember(project_id=1,user_id=u.id,role='rectifier',active=True))
        db.get(RectifyOrder,o['id']).assignee_id=ids[0];db.commit()
    result=f.c.post('/api/operations/handoff',headers=admin,json={'from_user':ids[0],'to_user':ids[1],
        'role':'rectifier','reason':'人员轮换交接'}).json()
    assert result['code']==0,result
    detail=f.c.get(f"/api/orders/{o['id']}",headers=f.h).json()['data']
    assert detail['assignee_id']==ids[1] and any(l['action']=='handoff' for l in detail['logs'])
    with SessionLocal() as db:
        from app.services.operations import ensure_handoff
        with pytest.raises(BizError):ensure_handoff(db,1,ids[1],'rectifier')

def test_unverifiable_model_citation_is_unknown(flow,monkeypatch):
    import json
    from app.services.ai_client import ai_client
    from app.services.analyze import analyze_record
    f=flow;r,_=f.record()
    monkeypatch.setattr(ai_client,'_api_key','test-only')
    monkeypatch.setattr(ai_client,'chat',lambda *a,**k:{'content':json.dumps({'has_defect':False,
        'defect_desc':'外观完整','severity':'无','basis':'不存在的规范条款9999','confidence':0.95,'suggestion':'常规巡查'})})
    with SessionLocal() as db:
        result,mode=analyze_record(r['id'],db)
    assert mode=='real' and result['has_defect'] is None and result['status']=='unknown'
