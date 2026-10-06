# P06 用例：调度任务注册 / 临期提醒 / 超期每日去重+系统超期日志 / 升级两档 / 失败重试退避与上限 / 催办限流（默认+可配置）/ 批量催办 / 通道降级
# 说明：测试环境未配置 WX_APPID / SMS_PROVIDER → wechat/sms 通道投递记 skipped（不阻断），即「通道降级」本身的验证。
from datetime import timedelta

import pytest

from app.database import SessionLocal
from app.models import Notification, OrderLog, RectifyOrder, User
from app.services.scheduler import (
    build_scheduler,
    escalate_overdue,
    retry_notifications,
    scan_overdue_orders,
    scan_warn_orders,
)
from app.utils import now_utc

# 种子用户：1=admin(组织+项目管理员) 2=李明(inspector) 4=赵强(rectifier) 7=陈静(P2 inspector) 8=赵敏(P2 rectifier)
ADMIN, LIMING, ZHAO, CHENJING, ZHAOMIN = 1, 2, 4, 7, 8


def test_stale_todo_messages_after_close(flow):
    """P2-1：工单闭环后，待办类消息标记 stale=true 且不计入未读角标；非待办类不受影响"""
    c, h = flow.client, flow.headers
    order = flow.create_order(assignee_id=ZHAO)

    # 闭环前：赵强的待办消息（ORDER_CREATED）未读且不 stale
    r = c.get("/api/messages?page_size=100", headers=h(ZHAO))
    assert r.json()["code"] == 0
    todo_msg = next(m for m in r.json()["data"]["list"]
                    if m["template_code"] == "ORDER_CREATED" and m["order_id"] == order["id"])
    assert todo_msg["stale"] is False and todo_msg["read"] is False

    # 走完整链路闭环：接收 → 开始 → 反馈 → 复核通过
    assert c.post(f"/api/orders/{order['id']}/accept", headers=h(ZHAO)).json()["code"] == 0
    assert c.post(f"/api/orders/{order['id']}/start", headers=h(ZHAO)).json()["code"] == 0
    att = flow.upload(h(ZHAO), "fb-stale.png", biz_type="order_feedback")
    assert c.post(f"/api/orders/{order['id']}/feedback",
                  json={"attachment_id": att, "text": "已加固"}, headers=h(ZHAO)).json()["code"] == 0
    assert c.post(f"/api/orders/{order['id']}/review",
                  json={"result": "pass"}, headers=h(LIMING)).json()["code"] == 0

    # 闭环后：待办类消息（待接收/待审核）标记 stale；闭环通知（非待办）不标记
    r = c.get("/api/messages?page_size=100", headers=h(ZHAO))
    msgs = r.json()["data"]["list"]
    closed_todo = [m for m in msgs if m["template_code"] in ("ORDER_CREATED", "ORDER_FEEDBACK")
                   and m["order_id"] == order["id"]]
    assert closed_todo and all(m["stale"] is True for m in closed_todo)
    pass_msg = next(m for m in msgs if m["template_code"] == "ORDER_REVIEW_PASS"
                    and m["order_id"] == order["id"])
    assert pass_msg["stale"] is False  # 状态通知类不参与 stale 判定

    # 未读角标：剔除 stale 后赵强仅剩闭环通知 1 条未读（其余消息均已因上述断言流程产生）
    r = c.get("/api/messages/unread-count", headers=h(ZHAO))
    unread = r.json()["data"]["count"]
    unread_list = [m for m in msgs if m["read"] is False and m["stale"] is not True]
    assert unread == len(unread_list)
    assert unread >= 1  # ORDER_REVIEW_PASS 仍在计数（非待办类不受影响）


@pytest.fixture()
def flow(client, auth_headers, png_bytes):
    """封装：上传附件 / 取检查项 / 建记录并 analyze / confirm 建工单（默认 P1，李明发起）"""

    def _upload(headers, name="p06.png", biz_type="record"):
        r = client.post("/api/attachments", data={"biz_type": biz_type, "source": "camera"},
                        files={"file": (name, png_bytes, "image/png")}, headers=headers)
        assert r.json()["code"] == 0, r.text
        return r.json()["data"]["id"]

    def _create_order(assignee_id, inspector_id=LIMING):
        headers = auth_headers(inspector_id)
        att_id = _upload(headers)
        r = client.get("/api/inspection-items?keyword=支护", headers=headers)
        item = r.json()["data"]["list"][0]
        r = client.post("/api/inspection-records", json={"item_id": item["id"], "attachment_id": att_id}, headers=headers)
        assert r.json()["code"] == 0
        record_id = r.json()["data"]["id"]
        r = client.post(f"/api/inspection-records/{record_id}/analyze", headers=headers)
        assert r.json()["code"] == 0
        r = client.post(f"/api/inspection-records/{record_id}/confirm",
                        json={"human_verdict": "abnormal", "note": "确认异常",
                              "order_draft": {"assignee_id": assignee_id, "problem_location": "东侧支护裂缝", "rectify_requirement": "加固后现场复核", "basis": "检查项依据"}},
                        headers=headers)
        assert r.json()["code"] == 0
        return r.json()["data"]["order"]

    class _NS:
        pass

    ns = _NS()
    ns.client = client
    ns.headers = auth_headers
    ns.upload = _upload
    ns.create_order = _create_order
    return ns


def _set_order_times(order_id: int, *, warn_at=None, deadline=None):
    """直接改库：挪 warn_at / deadline（模拟临期与超期状态）"""
    db = SessionLocal()
    try:
        o = db.get(RectifyOrder, order_id)
        if warn_at is not None:
            o.warn_at = warn_at
        if deadline is not None:
            o.deadline = deadline
        db.commit()
    finally:
        db.close()


def _notifs(order_id: int, template_code: str) -> list[Notification]:
    db = SessionLocal()
    try:
        return (db.query(Notification)
                .filter(Notification.biz_id == order_id, Notification.biz_type == "order",
                        Notification.template_code == template_code)
                .order_by(Notification.id)
                .all())
    finally:
        db.close()


def _event_key(n: Notification) -> str:
    """dedup_key 去掉通道后缀后的事件键（总纲 §7：事件键 + ':' + 通道）"""
    return n.dedup_key.rsplit(":", 1)[0]


# ---------- 1. 调度任务注册 ----------

def test_scheduler_registers_six_jobs():
    """六大任务全部注册（P06 §2.2）：15 分钟扫描×2 / 每小时升级 / 两个计划任务调度位 / 5 分钟重试"""
    s = build_scheduler()
    jobs = {j.id: str(j.trigger) for j in s.get_jobs()}
    assert set(jobs) == {
        "ai_jobs", "notification_outbox", "plan_recovery",
        "scan_warn_orders", "scan_overdue_orders", "escalate_overdue",
    }
    assert "interval" in jobs["scan_warn_orders"]
    assert "interval" in jobs["notification_outbox"]
    assert "interval" in jobs["plan_recovery"]


# ---------- 2. 临期提醒 ----------

def test_scan_warn_orders_notifies_assignee_with_dedup(flow):
    """warn_at 已过且未超期 → 责任人收 ORDER_WARN（inapp sent + wechat skipped）；
    重复扫描不重复轰炸（dedup_key=ORDER_WARN:{id} 终身一次）"""
    order = flow.create_order(assignee_id=ZHAO)
    _set_order_times(order["id"],
                     warn_at=now_utc() - timedelta(hours=1),
                     deadline=now_utc() + timedelta(hours=4))

    n1 = scan_warn_orders()
    # 本单 inapp + wechat 各一条；种子剧本含一张"即将超期"单（P1-5），首次扫描可能一并触发
    assert n1 >= 2

    rows = _notifs(order["id"], "ORDER_WARN")
    assert len(rows) == 2
    by_channel = {r.channel: r for r in rows}
    assert by_channel["inapp"].status == "sent"
    assert by_channel["wechat"].status == "skipped"  # 未配置 WX_APPID → 降级跳过，不抛错
    assert "微信" in (by_channel["wechat"].error or "")

    # 再扫两轮：零新增（终身一次去重）
    assert scan_warn_orders() == 0
    assert scan_warn_orders() == 0
    assert len(_notifs(order["id"], "ORDER_WARN")) == 2

    # 责任人消息中心可见，且未成功推送微信 → pushed=False（P06 §2.4.3）
    r = flow.client.get("/api/messages?page_size=100", headers=flow.headers(ZHAO))
    assert r.json()["code"] == 0
    hit = [m for m in r.json()["data"]["list"] if order["order_no"] in (m.get("title") or "")
           and "临期提醒" in m["title"]]
    assert hit and hit[0]["pushed"] is False


# ---------- 3. 超期提醒 + 每日去重 + 系统超期日志 ----------

def test_scan_overdue_daily_dedup_and_system_log(flow):
    """deadline 已过 → ORDER_OVERDUE（inapp+wechat+sms 三通道）；
    当天重复扫描零新增（dedup_key 含自然日）；首次超期写一条系统 escalate 日志"""
    order = flow.create_order(assignee_id=ZHAO)
    _set_order_times(order["id"], deadline=now_utc() - timedelta(hours=3))

    n1 = scan_overdue_orders()
    assert n1 >= 3  # 本单至少 3 条（可能含其他测试遗留超期单的首次通知）

    rows = _notifs(order["id"], "ORDER_OVERDUE")
    assert len(rows) == 3
    by_channel = {r.channel: r for r in rows}
    assert by_channel["inapp"].status == "sent"
    assert by_channel["wechat"].status == "skipped"
    assert by_channel["sms"].status == "skipped"  # 未配置短信服务商 → 降级跳过

    # 当天再扫两轮：本单零新增
    scan_overdue_orders()
    scan_overdue_orders()
    assert len(_notifs(order["id"], "ORDER_OVERDUE")) == 3

    # 首次超期写系统 escalate 日志：有且仅一条，operator_id 为空（系统动作）
    db = SessionLocal()
    try:
        logs = (db.query(OrderLog)
                .filter(OrderLog.order_id == order["id"], OrderLog.action == "escalate")
                .all())
        assert len(logs) == 1
        assert logs[0].operator_id is None
        assert logs[0].remark == "系统标记超期"
    finally:
        db.close()


# ---------- 4. 超期升级两档 ----------

def test_escalate_overdue_two_levels(flow):
    """超期 > escalate_after（high=4h）→ 项目管理员收 ORDER_ESCALATE（一档）；
    再超一档（> 8h）→ 组织管理员（非项目管理员的）追加收到（二档）；重复执行不重复通知"""
    # 造一个「仅组织管理员」用户（非任何项目管理员），验证二档触达
    db = SessionLocal()
    try:
        if db.query(User).filter(User.username == "orgadmin2").first() is None:
            db.add(User(username="orgadmin2", name="测试组织管理员", org_id=1,
                        password_hash="x-not-loginable", active=True, status="active",
                        is_org_admin=True, must_change_password=False, token_version=0))
            db.commit()
        orgadmin2 = db.query(User).filter(User.username == "orgadmin2").one()
        orgadmin2_id = orgadmin2.id
    finally:
        db.close()

    order = flow.create_order(assignee_id=ZHAO)
    oid = order["id"]

    # 一档：超期 5h > 4h → 项目管理员（admin，uid=1）
    _set_order_times(oid, deadline=now_utc() - timedelta(hours=5))
    escalate_overdue()
    level1 = [n for n in _notifs(oid, "ORDER_ESCALATE") if n.user_id == ADMIN]
    assert len(level1) >= 1
    assert all(_event_key(n).endswith(f":1:{ADMIN}") for n in level1)
    assert all("项目管理员" in n.content for n in level1)

    # 重复执行：一档不重复
    escalate_overdue()
    assert len([n for n in _notifs(oid, "ORDER_ESCALATE") if n.user_id == ADMIN]) == len(level1)
    # 二档未触发前：纯组织管理员不收
    assert _notifs(oid, "ORDER_ESCALATE") and not [
        n for n in _notifs(oid, "ORDER_ESCALATE") if n.user_id == orgadmin2_id]

    # 二档：超期 9h > 8h（2×escalate_after）→ 组织管理员追加
    _set_order_times(oid, deadline=now_utc() - timedelta(hours=9))
    escalate_overdue()
    level2 = [n for n in _notifs(oid, "ORDER_ESCALATE") if n.user_id == orgadmin2_id]
    assert level2, "二档应通知未在项目管理员之列的组织管理员"
    assert all(_event_key(n).endswith(f":2:{orgadmin2_id}") for n in level2)
    assert all("组织管理员" in n.content for n in level2)

    # 再执行：二档也不重复
    escalate_overdue()
    assert len([n for n in _notifs(oid, "ORDER_ESCALATE") if n.user_id == orgadmin2_id]) == len(level2)


# ---------- 5. 失败重试：指数退避 + 最多 3 次 ----------

def test_retry_notifications_backoff_and_cap(monkeypatch):
    """failed 且 retry_count<3 → 重试；指数退避窗口内跳过；满 3 次后不再重试"""
    import types

    from app.services import channels as channels_pkg

    # 伪短信通道：ready 通过、send 恒失败 → 重试后仍 failed（真实通道未配置时是 skipped）
    fake = types.ModuleType("fake_sms")
    fake.ready = lambda n, db: (True, None)
    fake.send = lambda n, db: (False, "模拟失败")
    monkeypatch.setitem(channels_pkg.CHANNELS, "sms", fake)

    db = SessionLocal()
    try:
        n = Notification(org_id=1, project_id=1, user_id=ZHAO, title="重试用例", content="c",
                         biz_type="order", biz_id=None, channel="sms",
                         template_code="ORDER_OVERDUE", status="failed",
                         retry_count=0, error="初始失败")
        db.add(n)
        db.commit()
        nid = n.id
        # updated_at 手动拨回 1 小时前（越过退避窗口）
        db.query(Notification).filter(Notification.id == nid).update(
            {"updated_at": now_utc() - timedelta(hours=1)})
        db.commit()
    finally:
        db.close()

    def _reset_window():
        db = SessionLocal()
        try:
            db.query(Notification).filter(Notification.id == nid).update(
                {"updated_at": now_utc() - timedelta(hours=1)})
            db.commit()
        finally:
            db.close()

    def _get():
        db = SessionLocal()
        try:
            return db.get(Notification, nid)
        finally:
            db.close()

    # 第 1 次重试：retry_count 0→1，状态仍 failed
    retried = retry_notifications()
    assert retried >= 1
    row = _get()
    assert row.retry_count == 1 and row.status == "failed" and row.error == "模拟失败"

    # 退避窗口内（刚更新过）立即再跑：跳过
    assert retry_notifications() == 0
    assert _get().retry_count == 1

    # 拨回窗口后：第 2、3 次重试
    _reset_window()
    retry_notifications()
    assert _get().retry_count == 2
    _reset_window()
    retry_notifications()
    assert _get().retry_count == 3

    # 满 3 次后：查询不再选中，永不重试
    _reset_window()
    retry_notifications()
    assert _get().retry_count == 3


# ---------- 6. 催办：默认 1 小时限流 + 窗口可配置 ----------

def test_urge_default_window_and_configurable(flow):
    """默认限流 1 小时：催后立即再催 → 429；SLA 规则可配置窗口（urge_interval_hours=2）后按新窗口生效"""
    order = flow.create_order(assignee_id=ZHAO)
    c = flow.client

    # 默认窗口（种子规则未配置 urge_interval_hours → 1 小时）
    r = c.post(f"/api/orders/{order['id']}/urge", json={}, headers=flow.headers(ADMIN))
    assert r.json()["code"] == 0, r.text
    r = c.post(f"/api/orders/{order['id']}/urge", json={}, headers=flow.headers(ADMIN))
    body = r.json()
    assert body["code"] == 429 and "1小时" in body["msg"], body

    # 组织级 high 规则改为 2 小时窗口（P06 §2.3.1 可配置）
    r = c.get("/api/admin/sla-rules", headers=flow.headers(ADMIN))
    assert r.json()["code"] == 0
    high_rule = [x for x in r.json()["data"] if x["project_id"] is None and x["risk_level"] == "high"][0]
    r = c.put(f"/api/admin/sla-rules/{high_rule['id']}", json={
        "project_id": None, "risk_level": "high", "defect_category": None,
        "sla_hours": high_rule["sla_hours"], "warn_ratio": high_rule["warn_ratio"],
        "escalate_after_hours": high_rule["escalate_after_hours"],
        "escalate_to_role": high_rule["escalate_to_role"],
        "urge_interval_hours": 2, "enabled": True,
    }, headers=flow.headers(ADMIN))
    assert r.json()["code"] == 0, r.text

    # 新工单按新窗口限流：催一次成功，立即再催 → 2 小时限流提示
    order2 = flow.create_order(assignee_id=ZHAO)
    r = c.post(f"/api/orders/{order2['id']}/urge", json={}, headers=flow.headers(ADMIN))
    assert r.json()["code"] == 0, r.text
    r = c.post(f"/api/orders/{order2['id']}/urge", json={}, headers=flow.headers(ADMIN))
    body = r.json()
    assert body["code"] == 429 and "2小时" in body["msg"], body

    # 催办走多通道：责任人收到 ORDER_URGE（inapp sent + wechat skipped）
    rows = _notifs(order2["id"], "ORDER_URGE")
    by_channel = {x.channel: x for x in rows}
    assert by_channel["inapp"].status == "sent"
    assert by_channel["wechat"].status == "skipped"


# ---------- 7. 批量催办 ----------

def test_urge_batch(flow):
    """批量催办：正常单催办成功；限流窗口内的单与跨项目/不存在的单返回跳过原因，不阻断其余"""
    c = flow.client
    order_a = flow.create_order(assignee_id=ZHAO)
    order_b = flow.create_order(assignee_id=ZHAO)

    # 先单独催 A（使其进入限流窗口）
    r = c.post(f"/api/orders/{order_a['id']}/urge", json={}, headers=flow.headers(ADMIN))
    assert r.json()["code"] == 0, r.text

    # 跨项目单：陈静在 P2 建单（admin 默认项目为 P1，批量催办按当前项目隔离）
    order_p2 = flow.create_order(assignee_id=ZHAOMIN, inspector_id=CHENJING)
    assert order_p2["project_id"] != order_a["project_id"]

    r = c.post("/api/admin/orders/urge-batch",
               json={"order_ids": [order_b["id"], order_a["id"], order_p2["id"], 999999]},
               headers=flow.headers(ADMIN))
    body = r.json()
    assert body["code"] == 0, body
    data = body["data"]
    urged_ids = {x["order_id"] for x in data["urged"]}
    skipped = {x["order_id"]: x["reason"] for x in data["skipped"]}
    assert order_b["id"] in urged_ids
    assert order_a["id"] not in urged_ids and "限流窗口" in skipped[order_a["id"]]
    assert order_p2["id"] not in urged_ids and "工单不存在" in skipped[order_p2["id"]]
    assert "工单不存在" in skipped[999999]

    # B 已催办：责任人收到 ORDER_URGE
    assert any(x.channel == "inapp" and x.status == "sent" for x in _notifs(order_b["id"], "ORDER_URGE"))

    # 入参校验：空列表 / 超 50 条 / 重复 id
    r = c.post("/api/admin/orders/urge-batch", json={"order_ids": []}, headers=flow.headers(ADMIN))
    assert r.json()["code"] == 1000
    r = c.post("/api/admin/orders/urge-batch", json={"order_ids": [order_b["id"], order_b["id"]]},
               headers=flow.headers(ADMIN))
    assert r.json()["code"] == 1000

    # 非管理员不得批量催办（require_perm 拒绝：HTTP 403 + detail.code，同 test_auth 惯例）
    r = c.post("/api/admin/orders/urge-batch", json={"order_ids": [order_b["id"]]},
               headers=flow.headers(ZHAO))
    assert r.status_code == 403 and r.json()["detail"]["code"] == 403


# ---------- 8. 管理端通知记录 + 手动重发 ----------

def test_admin_notifications_list_and_resend(flow):
    """通知记录可按状态/通道筛选；failed/skipped 可手动重发（重发 inapp 语义无意义，用 skipped wechat 验证）"""
    order = flow.create_order(assignee_id=ZHAO)
    # 未配置微信 → ORDER_CREATED 的 wechat 行为 skipped，可重发
    wechat_rows = [n for n in _notifs(order["id"], "ORDER_CREATED") if n.channel == "wechat"]
    assert wechat_rows and wechat_rows[0].status == "skipped"

    c = flow.client
    # 列表：按通道筛选 wechat + 模板筛选
    r = c.get("/api/admin/notifications", params={"channel": "wechat", "template_code": "ORDER_CREATED"},
              headers=flow.headers(ADMIN))
    assert r.json()["code"] == 0
    listed = r.json()["data"]["list"]
    assert any(x["id"] == wechat_rows[0].id for x in listed)
    assert all(x["channel"] == "wechat" for x in listed)

    # 按状态筛选 skipped
    r = c.get("/api/admin/notifications", params={"status": "skipped"}, headers=flow.headers(ADMIN))
    assert r.json()["code"] == 0
    assert all(x["status"] == "skipped" for x in r.json()["data"]["list"])

    # 手动重发：仍因未配置微信而 skipped（重发链路真实走通道，不抛错）
    r = c.post(f"/api/admin/notifications/{wechat_rows[0].id}/resend", headers=flow.headers(ADMIN))
    assert r.json()["code"] == 0, r.text
    assert r.json()["data"]["status"] == "skipped"

    # sent 状态不可重发（inapp 行已 sent）
    inapp_rows = [n for n in _notifs(order["id"], "ORDER_CREATED") if n.channel == "inapp"]
    r = c.post(f"/api/admin/notifications/{inapp_rows[0].id}/resend", headers=flow.headers(ADMIN))
    assert r.json()["code"] == 400

    # 非管理员无权访问通知记录（P2-2 契约：HTTP 403 包裹体）
    r = c.get("/api/admin/notifications", headers=flow.headers(ZHAO))
    assert r.status_code == 403 and r.json()["detail"]["code"] == 403
