# 工单状态机用例：合法全链路 / reject 打回 / 非法流转 / 消息断言
from datetime import timedelta

import pytest

from app.utils import now_utc

# 种子用户：1=admin 2=李明(inspector) 4=赵强(rectifier) 5=孙伟(rectifier)
LIMING, ZHAO, SUN = 2, 4, 5


@pytest.fixture()
def flow(client, auth_headers, png_bytes):
    """封装：上传附件 / 取检查项 / 建记录并 analyze / 建工单"""

    def _upload(headers, name="flow.png", biz_type="record", source="camera"):
        r = client.post("/api/attachments", data={"biz_type": biz_type, "source": source},
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

    def _messages(user_id, order_no):
        r = client.get("/api/messages?page_size=100", headers=auth_headers(user_id))
        assert r.json()["code"] == 0
        return [m for m in r.json()["data"]["list"] if order_no in (m.get("title") or "")]

    class _NS:
        pass
    ns = _NS()
    ns.client = client
    ns.headers = auth_headers
    ns.upload = lambda headers, name="flow.png", biz_type="record", source="camera": _upload(headers, name, biz_type, source)
    ns.create_order = lambda assignee_id, inspector_id=LIMING: _create_order(assignee_id, inspector_id)
    ns.messages = lambda user_id, order_no: _messages(user_id, order_no)
    return ns


def test_full_lifecycle(flow):
    """合法全链路：create→accept→start→feedback→review_pass + 消息接收人断言"""
    order = flow.create_order(assignee_id=ZHAO)
    order_id, order_no = order["id"], order["order_no"]
    assert order["status"] == "pending" and order["round"] == 1

    # 新工单 → 消息发给责任人赵强（李明不应收到该工单创建消息）
    zhao_msgs = flow.messages(ZHAO, order_no)
    liming_msgs = flow.messages(LIMING, order_no)
    assert any("新整改工单" in m["title"] for m in zhao_msgs)
    assert not any("新整改工单" in m["title"] for m in liming_msgs)

    r = flow.client.post(f"/api/orders/{order_id}/accept", headers=flow.headers(ZHAO))
    assert r.json()["data"]["status"] == "accepted"
    r = flow.client.post(f"/api/orders/{order_id}/start", headers=flow.headers(ZHAO))
    assert r.json()["data"]["status"] == "processing"

    att_id = flow.upload(flow.headers(ZHAO), "fb.png", biz_type="order_feedback")
    r = flow.client.post(f"/api/orders/{order_id}/feedback",
                         json={"attachment_id": att_id, "text": "已完成整改并复核"},
                         headers=flow.headers(ZHAO))
    data = r.json()["data"]
    assert data["status"] == "review"
    assert data["feedback_photos"] and data["feedback_photos"][0]["id"] == att_id

    # feedback → 消息发给发起人李明；此时李明对该工单恰有 3 条消息（接收/开始/待审核）
    liming_msgs = flow.messages(LIMING, order_no)
    titles = [m["title"] for m in liming_msgs]
    assert len(liming_msgs) == 3, titles
    assert any("已接收" in t for t in titles)
    assert any("开始整改" in t for t in titles)
    assert any("待您审核" in t for t in titles)
    # 赵强不应收到发给发起人的审核提醒
    assert not any("待您审核" in m["title"] for m in flow.messages(ZHAO, order_no))

    r = flow.client.post(f"/api/orders/{order_id}/review",
                         json={"result": "pass", "comment": "复核通过"}, headers=flow.headers(LIMING))
    data = r.json()["data"]
    assert data["status"] == "closed"
    assert data["closed_at"]
    # 闭环消息发给责任人赵强
    assert any("已闭环" in m["title"] for m in flow.messages(ZHAO, order_no))


def test_reject_round_and_feedback_reset(flow):
    """reject 后 round+1、当前轮次反馈清空；第一轮反馈附件归档保留（G4）"""
    order = flow.create_order(assignee_id=ZHAO)
    order_id, order_no = order["id"], order["order_no"]
    c = flow.client
    c.post(f"/api/orders/{order_id}/accept", headers=flow.headers(ZHAO))
    c.post(f"/api/orders/{order_id}/start", headers=flow.headers(ZHAO))
    att_id = flow.upload(flow.headers(ZHAO), "fb2.png", biz_type="order_feedback")
    r = c.post(f"/api/orders/{order_id}/feedback",
               json={"attachment_id": att_id, "text": "第一轮整改"}, headers=flow.headers(ZHAO))
    assert r.json()["data"]["status"] == "review"

    r = c.post(f"/api/orders/{order_id}/review",
               json={"result": "reject", "comment": "整改不到位"}, headers=flow.headers(LIMING))
    data = r.json()["data"]
    assert data["status"] == "processing"
    assert data["round"] == 2
    # 当前轮次（第 2 轮）尚无反馈 → 展示为空
    assert data["feedback_text"] is None
    assert data["feedback_photos"] == []
    # 打回消息发给责任人，且标题含第 2 轮
    reject_msgs = [m for m in flow.messages(ZHAO, order_no) if "被打回" in m["title"]]
    assert reject_msgs and "第 2 轮" in reject_msgs[0]["title"]
    # 第一轮反馈附件归档保留：仍存在、仍绑定本工单 round=1、可被责任人访问
    r = c.get(f"/api/attachments/{att_id}", headers=flow.headers(ZHAO))
    assert r.json()["code"] == 0, r.text
    meta = r.json()["data"]
    assert meta["biz_id"] == order_id and meta["round"] == 1 and meta["biz_type"] == "order_feedback"


def test_invalid_transitions(flow):
    """5 种非法流转均返回 BizError 400"""
    c = flow.client
    # 工单P：保持 pending
    order_p = flow.create_order(assignee_id=ZHAO)
    pid = order_p["id"]

    # 1. pending 直接 feedback → 400（每次用新上传的未绑定附件，避免先命中绑定校验）
    att = flow.upload(flow.headers(ZHAO), "fb3.png", biz_type="order_feedback")
    r = c.post(f"/api/orders/{pid}/feedback", json={"attachment_id": att, "text": "提前反馈"},
               headers=flow.headers(ZHAO))
    assert r.json()["code"] == 400
    # 2. pending 直接 review → 400
    r = c.post(f"/api/orders/{pid}/review", json={"result": "pass"}, headers=flow.headers(LIMING))
    assert r.json()["code"] == 400
    # 3. pending 直接 start → 400
    r = c.post(f"/api/orders/{pid}/start", headers=flow.headers(ZHAO))
    assert r.json()["code"] == 400

    # 工单A：推进到 accepted
    order_a = flow.create_order(assignee_id=SUN)
    aid = order_a["id"]
    c.post(f"/api/orders/{aid}/accept", headers=flow.headers(SUN))
    # 4. accepted 直接 feedback → 400
    att = flow.upload(flow.headers(SUN), "fb4.png", biz_type="order_feedback")
    r = c.post(f"/api/orders/{aid}/feedback", json={"attachment_id": att, "text": "跳过开始"},
               headers=flow.headers(SUN))
    assert r.json()["code"] == 400

    # 工单C：推进到 closed
    order_c = flow.create_order(assignee_id=ZHAO)
    cid = order_c["id"]
    c.post(f"/api/orders/{cid}/accept", headers=flow.headers(ZHAO))
    c.post(f"/api/orders/{cid}/start", headers=flow.headers(ZHAO))
    att = flow.upload(flow.headers(ZHAO), "fb5.png", biz_type="order_feedback")
    c.post(f"/api/orders/{cid}/feedback", json={"attachment_id": att, "text": "整改完成"},
           headers=flow.headers(ZHAO))
    c.post(f"/api/orders/{cid}/review", json={"result": "pass"}, headers=flow.headers(LIMING))
    # 5. closed 后 accept → 400
    r = c.post(f"/api/orders/{cid}/accept", headers=flow.headers(ZHAO))
    assert r.json()["code"] == 400
    # closed 后 feedback → 400
    att = flow.upload(flow.headers(ZHAO), "fb6.png", biz_type="order_feedback")
    r = c.post(f"/api/orders/{cid}/feedback", json={"attachment_id": att, "text": "闭环后再反馈"},
               headers=flow.headers(ZHAO))
    assert r.json()["code"] == 400


def test_validation_error_names_field(flow):
    """P1-2：参数校验错误指明字段与原因（HTTP 200 + code=1000 + 中文 msg）"""
    c = flow.client
    order = flow.create_order(assignee_id=ZHAO)
    c.post(f"/api/orders/{order['id']}/accept", headers=flow.headers(ZHAO))
    c.post(f"/api/orders/{order['id']}/start", headers=flow.headers(ZHAO))

    # feedback 错用 note 字段 → 指明 text 必填
    r = c.post(f"/api/orders/{order['id']}/feedback", json={"note": "x"}, headers=flow.headers(ZHAO))
    body = r.json()
    assert r.status_code == 200 and body["code"] == 1000
    assert "字段 text 必填" in body["msg"]

    # review 非法枚举值 → 指明 result 只能为 pass/reject（自定义校验器中文 msg 保留）
    r = c.post(f"/api/orders/{order['id']}/review", json={"result": "approved"},
               headers=flow.headers(LIMING))
    body = r.json()
    assert r.status_code == 200 and body["code"] == 1000
    assert "result 只能为 pass/reject" in body["msg"]
    assert "approved" in body["msg"]  # 当前值回显便于排查


def test_transfer_order(flow):
    """转派：状态保持不变、责任人改派、双端通知、日志可溯（管理员功能补充）"""
    order = flow.create_order(assignee_id=ZHAO)
    order_id, order_no, version = order["id"], order["order_no"], order["version"]
    c = flow.client

    # 发起人李明把赵强的工单转派给孙伟
    r = c.post(f"/api/orders/{order_id}/transfer",
               json={"assignee_id": SUN, "reason": "赵强请假", "expected_version": version},
               headers=flow.headers(LIMING))
    assert r.json()["code"] == 0, r.text
    data = r.json()["data"]
    assert data["status"] == "pending"          # 状态保持不变
    assert data["assignee_id"] == SUN           # 责任人已改派
    assert data["version"] == version + 1       # 乐观锁版本+1

    # 原/新责任人均收到改派通知；操作人李明不重复接收
    assert any("已改派" in m["title"] for m in flow.messages(ZHAO, order_no))
    assert any("已改派" in m["title"] for m in flow.messages(SUN, order_no))
    assert not any("已改派" in m["title"] for m in flow.messages(LIMING, order_no))

    # 新责任人可直接接收（从当前进度继续）
    r = c.post(f"/api/orders/{order_id}/accept", headers=flow.headers(SUN))
    assert r.json()["data"]["status"] == "accepted"

    # 详情日志含转派记录与原因
    r = c.get(f"/api/orders/{order_id}", headers=flow.headers(LIMING))
    logs = r.json()["data"]["logs"]
    transfer_logs = [lg for lg in logs if lg["action"] == "transfer"]
    assert transfer_logs and "赵强请假" in transfer_logs[0]["remark"]


def test_transfer_order_invalid(flow):
    """转派非法场景：同人转派 400 / 非本项目责任人 1011 / 已闭环 400 / 责任人无权限 403 / 版本冲突 1014"""
    c = flow.client
    order = flow.create_order(assignee_id=ZHAO)
    order_id, version = order["id"], order["version"]

    # 1. 转派给当前责任人本人 → 400
    r = c.post(f"/api/orders/{order_id}/transfer", json={"assignee_id": ZHAO},
               headers=flow.headers(LIMING))
    assert r.json()["code"] == 400
    # 2. 目标不是本项目 rectifier（李明是巡查员）→ 1011
    r = c.post(f"/api/orders/{order_id}/transfer", json={"assignee_id": LIMING},
               headers=flow.headers(LIMING))
    assert r.json()["code"] == 1011
    # 3. 责任人赵强无转派权限（order:transfer 不含 rectifier）→ 403
    r = c.post(f"/api/orders/{order_id}/transfer", json={"assignee_id": SUN},
               headers=flow.headers(ZHAO))
    assert r.status_code == 403
    # 4. expected_version 过期 → 1014
    r = c.post(f"/api/orders/{order_id}/transfer",
               json={"assignee_id": SUN, "expected_version": version + 99},
               headers=flow.headers(LIMING))
    assert r.json()["code"] == 1014
    # 5. 待审核状态不可转派 → 400
    c.post(f"/api/orders/{order_id}/accept", headers=flow.headers(ZHAO))
    c.post(f"/api/orders/{order_id}/start", headers=flow.headers(ZHAO))
    att = flow.upload(flow.headers(ZHAO), "tf.png", biz_type="order_feedback")
    c.post(f"/api/orders/{order_id}/feedback",
           json={"attachment_id": att, "text": "整改完成"}, headers=flow.headers(ZHAO))
    r = c.post(f"/api/orders/{order_id}/transfer", json={"assignee_id": SUN},
               headers=flow.headers(LIMING))
    assert r.json()["code"] == 400


def test_cancel_order(flow):
    """作废：pending → cancelled 终态、责任人收通知、后续动作全部拦截"""
    order = flow.create_order(assignee_id=ZHAO)
    order_id, order_no, version = order["id"], order["order_no"], order["version"]
    c = flow.client

    # 发起人李明作废工单（原因必填）
    r = c.post(f"/api/orders/{order_id}/cancel",
               json={"reason": "现场复核为误报，误建工单", "expected_version": version},
               headers=flow.headers(LIMING))
    assert r.json()["code"] == 0, r.text
    data = r.json()["data"]
    assert data["status"] == "cancelled" and data["version"] == version + 1

    # 责任人赵强收到作废通知；操作人李明不重复接收
    assert any("已作废" in m["title"] for m in flow.messages(ZHAO, order_no))
    assert not any("已作废" in m["title"] for m in flow.messages(LIMING, order_no))

    # 终态后：重复作废 400 / 接收 400 / 催办 400 / 转派 400
    assert c.post(f"/api/orders/{order_id}/cancel", json={"reason": "再次作废"},
                  headers=flow.headers(LIMING)).json()["code"] == 400
    assert c.post(f"/api/orders/{order_id}/accept", headers=flow.headers(ZHAO)).json()["code"] == 400
    assert c.post(f"/api/orders/{order_id}/urge", headers=flow.headers(1)).json()["code"] == 400
    assert c.post(f"/api/orders/{order_id}/transfer", json={"assignee_id": SUN},
                  headers=flow.headers(LIMING)).json()["code"] == 400

    # 日志记录作废动作与原因
    r = c.get(f"/api/orders/{order_id}", headers=flow.headers(LIMING))
    cancel_logs = [lg for lg in r.json()["data"]["logs"] if lg["action"] == "cancel"]
    assert cancel_logs and "误报" in cancel_logs[0]["remark"]


def test_cancel_order_permission(flow):
    """作废权限：责任人无权作废（403）；作废原因必填（1000）"""
    c = flow.client
    order = flow.create_order(assignee_id=ZHAO)
    order_id = order["id"]

    # 责任人赵强无 order:cancel 权限 → 403
    r = c.post(f"/api/orders/{order_id}/cancel", json={"reason": "不想干了"},
               headers=flow.headers(ZHAO))
    assert r.status_code == 403
    # 原因缺失 → 1000 指明字段
    r = c.post(f"/api/orders/{order_id}/cancel", json={"reason": " "},
               headers=flow.headers(LIMING))
    body = r.json()
    assert r.status_code == 200 and body["code"] == 1000
    assert "作废原因必填" in body["msg"]


def test_dashboard_sla_on_time_hint(flow):
    """P1-3：存在未闭环超期单时，看板返回口径说明（解释 100% 与超期清单并存的矛盾）"""
    from app.database import SessionLocal
    from app.models import RectifyOrder

    order = flow.create_order(assignee_id=ZHAO)  # pending 未闭环
    # 将 deadline 置于过去 → 成为超期未闭环单（不计入按期完成率分母）
    db = SessionLocal()
    try:
        row = db.get(RectifyOrder, order["id"])
        row.deadline = now_utc() - timedelta(hours=3)
        db.commit()
    finally:
        db.close()

    r = flow.client.get("/api/admin/dashboard?range=7d", headers=flow.headers(1))
    assert r.json()["code"] == 0, r.text
    data = r.json()["data"]
    assert data["overdue_total"] >= 1
    hint = data["metrics"]["sla_on_time_hint"]
    assert "已闭环工单中按期完成的比例" in hint
    assert "未闭环且已超期，不计入分母" in hint
