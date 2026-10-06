# P05 数据正确性与并发用例：D3 撞号 / D4 重复确认与幂等键 / D5 SLA 时限 / D6-D7 分页 / D10 乐观锁
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from app.database import SessionLocal
from app.models import AiFeedback, Project, RectifyOrder
from app.services.ratelimit import _local_buckets
from app.utils import as_utc, now_utc

# 种子用户：1=admin(project_admin) 2=李明(inspector) 3=王芳(inspector) 4=赵强(rectifier)
ADMIN, LIMING, WANGFANG, ZHAO = 1, 2, 3, 4


@pytest.fixture(autouse=True)
def _reset_upload_ratelimit():
    """清空进程内上传限流桶：本文件大量上传，避免跨用例累计触发 60 次/分钟"""
    _local_buckets.clear()
    yield
    _local_buckets.clear()


@pytest.fixture()
def flow(client, auth_headers, png_bytes):
    """封装：上传附件 / 建记录并 analyze（未确认）/ confirm 建单"""

    def _upload(headers):
        r = client.post("/api/attachments", data={"biz_type": "record", "source": "camera"},
                        files={"file": ("p05.png", png_bytes, "image/png")}, headers=headers)
        assert r.json()["code"] == 0, r.text
        return r.json()["data"]["id"]

    def _prepare_record(inspector_id=LIMING, keyword="支护"):
        headers = auth_headers(inspector_id)
        att_id = _upload(headers)
        r = client.get(f"/api/inspection-items?keyword={keyword}", headers=headers)
        assert r.json()["code"] == 0, r.text
        items = r.json()["data"]["list"]
        assert items, f"keyword={keyword} 无匹配检查项"
        r = client.post("/api/inspection-records",
                        json={"item_id": items[0]["id"], "attachment_id": att_id}, headers=headers)
        assert r.json()["code"] == 0, r.text
        record_id = r.json()["data"]["id"]
        r = client.post(f"/api/inspection-records/{record_id}/analyze", headers=headers)
        assert r.json()["code"] == 0, r.text
        return record_id

    def _confirm(record_id, headers, assignee_id=ZHAO):
        return client.post(f"/api/inspection-records/{record_id}/confirm",
                           json={"human_verdict": "abnormal",
                                 "order_draft": {"assignee_id": assignee_id, "problem_location": "东侧支护裂缝", "rectify_requirement": "加固后现场复核", "basis": "检查项依据"}},
                           headers=headers)

    class _NS:
        pass

    ns = _NS()
    ns.client = client
    ns.headers = auth_headers
    ns.upload = _upload
    ns.prepare_record = _prepare_record
    ns.confirm = _confirm
    return ns


def _db():
    return SessionLocal()


def _order_row(order_id):
    db = _db()
    try:
        return db.get(RectifyOrder, order_id)
    finally:
        db.close()


# ---------- D4：重复确认幂等 ----------

def test_duplicate_confirm_rejected(flow):
    """同一记录二次确认 → 1012；库中仅 1 张工单、1 条 ai_feedback"""
    headers = flow.headers(LIMING)
    rec = flow.prepare_record()
    r1 = flow.confirm(rec, headers)
    assert r1.json()["code"] == 0, r1.text
    order_id = r1.json()["data"]["order"]["id"]

    r2 = flow.confirm(rec, headers)
    assert r2.json()["code"] == 1012

    db = _db()
    try:
        assert db.query(RectifyOrder).filter(RectifyOrder.record_id == rec).count() == 1
        assert db.query(AiFeedback).filter(AiFeedback.record_id == rec).count() == 1
    finally:
        db.close()
    assert _order_row(order_id) is not None


def test_idempotency_key_replay(flow):
    """携带同一 Idempotency-Key 重试 → 重放首次响应（code=0 且同一工单），不产生第二张单"""
    headers = dict(flow.headers(LIMING))
    headers["Idempotency-Key"] = "p05-test-key-001"
    rec = flow.prepare_record()
    r1 = flow.confirm(rec, headers)
    assert r1.json()["code"] == 0, r1.text
    order_id = r1.json()["data"]["order"]["id"]

    r2 = flow.confirm(rec, headers)
    assert r2.json()["code"] == 0, r2.text
    assert r2.json()["data"]["order"]["id"] == order_id  # 重放首次响应

    db = _db()
    try:
        assert db.query(RectifyOrder).filter(RectifyOrder.record_id == rec).count() == 1
        assert db.query(AiFeedback).filter(AiFeedback.record_id == rec).count() == 1
    finally:
        db.close()


def test_concurrent_confirm_single_order(flow):
    """8 并发确认同一记录 → 恰 1 次成功，其余 1012；库中仅 1 张工单、1 条 ai_feedback"""
    headers = flow.headers(LIMING)
    rec = flow.prepare_record()
    n = 8
    barrier = threading.Barrier(n)
    codes = []

    def worker():
        barrier.wait()
        codes.append(flow.confirm(rec, headers).json()["code"])

    with ThreadPoolExecutor(max_workers=n) as ex:
        for _ in range(n):
            ex.submit(worker)

    assert codes.count(0) == 1, codes
    assert all(c == 1012 for c in codes if c != 0), codes
    db = _db()
    try:
        assert db.query(RectifyOrder).filter(RectifyOrder.record_id == rec).count() == 1
        assert db.query(AiFeedback).filter(AiFeedback.record_id == rec).count() == 1
    finally:
        db.close()


# ---------- D3：并发建单不撞号 ----------

def test_next_order_no_atomic():
    """order_no_seq 原子取号：20 线程各自独立会话取号 → 序号连续且互不相同"""
    from app.services.order_flow import next_order_no

    n = 20
    results = []
    lock = threading.Lock()

    def worker():
        s = SessionLocal()
        try:
            p = s.get(Project, 1)
            no = next_order_no(s, p)
            s.commit()
            with lock:
                results.append(no)
        finally:
            s.close()

    with ThreadPoolExecutor(max_workers=n) as ex:
        for _ in range(n):
            ex.submit(worker)

    assert len(results) == n
    assert len(set(results)) == n, results  # 无撞号
    seqs = sorted(int(no.rsplit("-", 1)[1]) for no in results)
    assert seqs == list(range(seqs[0], seqs[0] + n))  # 连续无跳号（无失败回滚造成的空洞）


def test_concurrent_create_unique_order_no(flow):
    """10 并发各自确认建单 → 全部成功且 order_no 互不相同，无 500"""
    headers = flow.headers(LIMING)
    recs = [flow.prepare_record() for _ in range(10)]
    n = len(recs)
    barrier = threading.Barrier(n)
    payloads = []

    def worker(rec):
        barrier.wait()
        payloads.append(flow.confirm(rec, headers).json())

    with ThreadPoolExecutor(max_workers=n) as ex:
        for rec in recs:
            ex.submit(worker, rec)

    assert all(p["code"] == 0 for p in payloads), [p for p in payloads if p["code"] != 0]
    nos = [p["data"]["order"]["order_no"] for p in payloads]
    assert len(set(nos)) == n, nos


# ---------- D5：SLA 配置驱动时限 ----------

def test_sla_deadline_by_risk(flow):
    """high 风险项 → 8h（warn 6h）；low 风险项 → 168h；risk_level/severity 快照入单"""
    def _diff_seconds(a, b):
        return (as_utc(a) - as_utc(b)).total_seconds()

    headers = flow.headers(LIMING)

    rec_high = flow.prepare_record(keyword="支护")  # 基坑支护结构完整性检查（high）
    r = flow.confirm(rec_high, headers)
    assert r.json()["code"] == 0, r.text
    o = _order_row(r.json()["data"]["order"]["id"])
    assert o.risk_level == "high"
    assert o.sla_hours == 8
    # deadline 基准为 confirm 时刻，created_at 为入库时刻（SQLite 秒截断）→ 允许 ±2s
    assert abs(_diff_seconds(o.deadline, o.created_at) - 8 * 3600) <= 2
    assert abs(_diff_seconds(o.warn_at, o.created_at) - 6 * 3600) <= 2  # 0.75 × 8h

    rec_low = flow.prepare_record(keyword="材料堆放")  # 建筑材料堆放与标识检查（low）
    r = flow.confirm(rec_low, headers)
    assert r.json()["code"] == 0, r.text
    o2 = _order_row(r.json()["data"]["order"]["id"])
    assert o2.risk_level == "low"
    assert o2.sla_hours == 168
    assert abs(_diff_seconds(o2.deadline, o2.created_at) - 168 * 3600) <= 2
    assert abs(_diff_seconds(o2.warn_at, o2.created_at) - 126 * 3600) <= 2  # 0.75 × 168h
    assert o2.severity  # severity 快照自 AI 判定（mock 也有值）


def test_sla_rule_change_only_affects_new(flow):
    """改组织级 medium 规则 48→24h：存量单 48h 不变，新单 24h 生效（时限固化原则）"""
    admin = flow.headers(ADMIN)
    r = flow.client.get("/api/admin/sla-rules", headers=admin)
    assert r.json()["code"] == 0, r.text
    rules = r.json()["data"]
    medium_rule = next(x for x in rules
                       if x["risk_level"] == "medium" and x["project_id"] is None)
    original = {k: medium_rule[k] for k in (
        "project_id", "risk_level", "defect_category", "sla_hours", "warn_ratio",
        "escalate_after_hours", "escalate_to_role", "enabled")}

    headers = flow.headers(LIMING)
    rec_old = flow.prepare_record(keyword="降水")  # 基坑降水与排水检查（medium）
    r_old = flow.confirm(rec_old, headers)
    old_order_id = r_old.json()["data"]["order"]["id"]

    changed = dict(original, sla_hours=24)
    r = flow.client.put(f"/api/admin/sla-rules/{medium_rule['id']}", json=changed, headers=admin)
    assert r.json()["code"] == 0, r.text
    try:
        rec_new = flow.prepare_record(keyword="降水")
        r_new = flow.confirm(rec_new, headers)
        new_order_id = r_new.json()["data"]["order"]["id"]

        assert _order_row(old_order_id).sla_hours == 48  # 存量固化
        assert _order_row(new_order_id).sla_hours == 24  # 新单生效
    finally:
        rr = flow.client.put(f"/api/admin/sla-rules/{medium_rule['id']}", json=original, headers=admin)
        assert rr.json()["code"] == 0, rr.text


# ---------- D10：乐观锁 ----------

def test_optimistic_lock_1014(flow):
    """expected_version 不匹配 → 1014；匹配 → 成功且 version+1；不携带 → 兼容旧客户端"""
    headers = flow.headers(LIMING)
    zhao = flow.headers(ZHAO)
    rec = flow.prepare_record()
    r = flow.confirm(rec, headers)
    order_id = r.json()["data"]["order"]["id"]
    assert r.json()["data"]["order"]["version"] == 0

    # 错误版本 → 1014，状态不变
    r = flow.client.post(f"/api/orders/{order_id}/accept", json={"expected_version": 99}, headers=zhao)
    assert r.json()["code"] == 1014
    assert _order_row(order_id).status == "pending"

    # 正确版本 → 成功，version=1
    r = flow.client.post(f"/api/orders/{order_id}/accept", json={"expected_version": 0}, headers=zhao)
    assert r.json()["code"] == 0, r.text
    assert r.json()["data"]["version"] == 1

    # 旧版本重放（他人已更新）→ 1014
    r = flow.client.post(f"/api/orders/{order_id}/start", json={"expected_version": 0}, headers=zhao)
    assert r.json()["code"] == 1014

    # 不携带版本 → 兼容旧客户端，成功且 version=2
    r = flow.client.post(f"/api/orders/{order_id}/start", headers=zhao)
    assert r.json()["code"] == 0, r.text
    assert r.json()["data"]["version"] == 2


def test_concurrent_transition_single_winner(flow):
    """20 并发对同一 pending 单 accept → 恰 1 成功，其余 400/1014，无 500，版本无错乱"""
    headers = flow.headers(LIMING)
    zhao = flow.headers(ZHAO)
    rec = flow.prepare_record()
    r = flow.confirm(rec, headers)
    order_id = r.json()["data"]["order"]["id"]

    n = 20
    barrier = threading.Barrier(n)
    results = []

    def worker():
        barrier.wait()
        resp = flow.client.post(f"/api/orders/{order_id}/accept",
                                json={"expected_version": 0}, headers=zhao)
        results.append(resp.json()["code"])

    with ThreadPoolExecutor(max_workers=n) as ex:
        for _ in range(n):
            ex.submit(worker)

    assert results.count(0) == 1, results
    assert all(c in (400, 1014) for c in results if c != 0), results
    o = _order_row(order_id)
    assert o.status == "accepted" and o.version == 1  # 无状态错乱


# ---------- D6/D7：数据库层分页 ----------

def test_admin_orders_db_pagination_and_overdue(flow):
    """监督列表 offset/limit 分页翻页无重叠；overdue=true 走 SQL 过滤"""
    headers = flow.headers(LIMING)
    admin = flow.headers(ADMIN)
    order_ids = []
    for _ in range(3):
        rec = flow.prepare_record()
        r = flow.confirm(rec, headers)
        order_ids.append(r.json()["data"]["order"]["id"])

    r = flow.client.get("/api/admin/orders?page=1&page_size=2", headers=admin)
    assert r.json()["code"] == 0, r.text
    d1 = r.json()["data"]
    assert d1["page_size"] == 2 and len(d1["list"]) == 2 and d1["total"] >= 3

    r = flow.client.get("/api/admin/orders?page=2&page_size=2", headers=admin)
    d2 = r.json()["data"]
    ids1 = {o["id"] for o in d1["list"]}
    ids2 = {o["id"] for o in d2["list"]}
    assert not (ids1 & ids2)  # 翻页无重叠
    assert set(order_ids) <= ids1 | ids2 | {o["id"] for o in
                                            flow.client.get("/api/admin/orders?page=3&page_size=2",
                                                            headers=admin).json()["data"]["list"]}

    # 无超期单时 overdue=true 为空
    r = flow.client.get("/api/admin/orders?overdue=true", headers=admin)
    assert r.json()["code"] == 0
    assert all(o["id"] not in order_ids for o in r.json()["data"]["list"])

    # 直接改库造一张超期单（deadline 置为 2 小时前）→ SQL 过滤命中且带 overdue_hours
    db = _db()
    try:
        o = db.get(RectifyOrder, order_ids[0])
        o.deadline = now_utc() - timedelta(hours=2)
        db.commit()
    finally:
        db.close()
    r = flow.client.get("/api/admin/orders?overdue=true", headers=admin)
    overdue_ids = [o["id"] for o in r.json()["data"]["list"]]
    assert order_ids[0] in overdue_ids
    assert all(o["id"] not in order_ids[1:] for o in r.json()["data"]["list"])
    hit = next(o for o in r.json()["data"]["list"] if o["id"] == order_ids[0])
    assert 1.9 <= hit["overdue_hours"] <= 2.1
