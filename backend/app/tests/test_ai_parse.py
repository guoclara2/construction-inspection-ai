# AI 解析与 schema 校验用例（纯函数 + mock 稳定性）+ P0-4 无效判别拦截
import json

from app.database import SessionLocal
from app.models import InspectionRecord
from app.services.analyze import is_actionable_ai_result, parse_json_block, validate_ai_result
from app.services.ai_client import MOCK_ANALYZE_RESULTS

VALID_RESULT = {
    "has_defect": True,
    "defect_desc": "支护结构可见斜向裂缝",
    "severity": "中度",
    "basis": "JGJ 59-2011",
    "confidence": 0.72,
    "suggestion": "设置观测标记监测",
}


# ---------- parse_json_block 四例 ----------

def test_parse_pure_json():
    import json
    content = json.dumps(VALID_RESULT, ensure_ascii=False)
    obj = parse_json_block(content)
    assert obj == VALID_RESULT


def test_parse_markdown_json_block():
    import json
    content = "分析结果如下：\n```json\n" + json.dumps(VALID_RESULT, ensure_ascii=False) + "\n```\n请参考。"
    obj = parse_json_block(content)
    assert obj == VALID_RESULT


def test_parse_with_surrounding_text():
    import json
    content = "前置说明文字。结果：" + json.dumps(VALID_RESULT, ensure_ascii=False) + " 以上为结论，含 }{ 干扰字符。"
    obj = parse_json_block(content)
    assert obj == VALID_RESULT


def test_parse_garbage_text():
    assert parse_json_block("这不是 JSON，只是普通文字。") is None
    assert parse_json_block("") is None
    assert parse_json_block("```json\n不是对象\n```") is None
    assert parse_json_block("{broken json") is None


def test_parse_nested_and_string_braces():
    # 字符串内的大括号不应干扰平衡计算
    content = '{"defect_desc": "裂缝宽度 {约2mm} 处", "severity": "中度"}'
    obj = parse_json_block(content)
    assert obj is not None and obj["defect_desc"].endswith("处")


# ---------- schema 校验 ----------

def test_validate_ok():
    assert validate_ai_result(VALID_RESULT) is True
    assert validate_ai_result(None) is False


def test_validate_confidence_out_of_range():
    for bad in (1.5, -0.1):
        obj = dict(VALID_RESULT, confidence=bad)
        assert validate_ai_result(obj) is False
    assert validate_ai_result(dict(VALID_RESULT, confidence="高")) is False


def test_validate_severity_illegal():
    obj = dict(VALID_RESULT, severity="超级严重")
    assert validate_ai_result(obj) is False
    obj = dict(VALID_RESULT, severity=None)
    assert validate_ai_result(obj) is False


def test_validate_missing_or_bad_fields():
    # has_defect 非 bool
    assert validate_ai_result(dict(VALID_RESULT, has_defect="true")) is False
    # 缺字段
    for key in ("has_defect", "defect_desc", "basis", "suggestion", "confidence", "severity"):
        obj = {k: v for k, v in VALID_RESULT.items() if k != key}
        assert validate_ai_result(obj) is False, f"缺 {key} 应无效"
    # 字段类型错误
    assert validate_ai_result(dict(VALID_RESULT, defect_desc=123)) is False


# ---------- mock analyze 稳定性（同 record_id 二次调用一致） ----------

def test_mock_analyze_stable(client, auth_headers, png_bytes):
    liming = auth_headers(2)
    r = client.post("/api/attachments", data={"biz_type": "record", "source": "camera"},
                    files={"file": ("ai_test.png", png_bytes, "image/png")}, headers=liming)
    att_id = r.json()["data"]["id"]
    r = client.get("/api/inspection-items?keyword=支护", headers=liming)
    item = r.json()["data"]["list"][0]
    r = client.post("/api/inspection-records", json={"item_id": item["id"], "attachment_id": att_id}, headers=liming)
    record_id = r.json()["data"]["id"]

    r1 = client.post(f"/api/inspection-records/{record_id}/analyze", headers=liming)
    assert r1.json()["code"] == 0
    data1 = r1.json()["data"]
    assert data1["mode"] == "mock"
    assert validate_ai_result(data1["ai_result"]) is True

    # 同记录重复 analyze：结果与 MOCK 预设（record_id % 3）一致
    r2 = client.post(f"/api/inspection-records/{record_id}/analyze", headers=liming)
    data2 = r2.json()["data"]
    assert data2 == data1
    assert data1["ai_result"] == MOCK_ANALYZE_RESULTS[record_id % 3]


# ---------- P0-4：AI 判别可采信性（is_actionable_ai_result） ----------

def test_is_actionable_ai_result():
    assert is_actionable_ai_result(VALID_RESULT) is True           # confidence 0.72、正常描述
    assert is_actionable_ai_result(None) is False
    assert is_actionable_ai_result({}) is False
    assert is_actionable_ai_result(dict(VALID_RESULT, confidence="高")) is False
    # 低置信度 / 降级结果（confidence=0）不可采信
    assert is_actionable_ai_result(dict(VALID_RESULT, confidence=0.59)) is False
    assert is_actionable_ai_result(dict(VALID_RESULT, confidence=0)) is False
    # 无效结论关键词（纯色/模糊/信息不足照片的典型返回）不可采信
    for bad_desc in ("照片为纯色背景，无任何现场影像信息，无法判断", "信息不足，请重新提供照片", "照片模糊"):
        assert is_actionable_ai_result(dict(VALID_RESULT, defect_desc=bad_desc)) is False
    # 高置信度且无关键词 → 可采信
    assert is_actionable_ai_result(dict(VALID_RESULT, confidence=0.6)) is True


def _invalid_ai_record(client, auth_headers, png_bytes):
    """构造已 analyze 的记录并注入 real 模式纯色照片的典型无效判别结果，返回 (record_id, item_name)"""
    liming = auth_headers(2)
    r = client.post("/api/attachments", data={"biz_type": "record", "source": "camera"},
                    files={"file": ("plain.png", png_bytes, "image/png")}, headers=liming)
    att_id = r.json()["data"]["id"]
    r = client.get("/api/inspection-items?keyword=支护", headers=liming)
    item = r.json()["data"]["list"][0]
    r = client.post("/api/inspection-records", json={"item_id": item["id"], "attachment_id": att_id},
                    headers=liming)
    record_id = r.json()["data"]["id"]
    r = client.post(f"/api/inspection-records/{record_id}/analyze", headers=liming)
    assert r.json()["code"] == 0
    assert r.json()["data"]["actionable"] is True  # mock 预设全部可采信
    invalid = {"has_defect": False,
               "defect_desc": "照片为纯色背景，无任何现场影像信息，无法判断",
               "severity": "无", "basis": "", "confidence": 0.0, "suggestion": ""}
    db = SessionLocal()
    try:
        rec = db.get(InspectionRecord, record_id)
        rec.ai_result = json.dumps(invalid, ensure_ascii=False)
        db.commit()
    finally:
        db.close()
    return record_id, item["name"]


def test_confirm_invalid_ai_not_into_order(client, auth_headers, png_bytes):
    """P0-4：无效判别（纯色照片）→ 建单后工单三段绝不引用 AI 废话文本"""
    record_id, item_name = _invalid_ai_record(client, auth_headers, png_bytes)
    r = client.post(f"/api/inspection-records/{record_id}/confirm",
                    json={"human_verdict": "abnormal", "note": "现场目视发现支撑处异常",
                          "order_draft": {"assignee_id": 4, "problem_location": "东侧支护裂缝", "rectify_requirement": "加固后现场复核", "basis": "检查项依据"}},
                    headers=auth_headers(2))
    assert r.json()["code"] == 0, r.text
    data = r.json()["data"]
    assert data["ai_discarded"] is True
    order = data["order"]
    # 问题定位 = 检查项名 + 巡查员备注；整改要求留空由巡查员手填
    assert order["problem_location"] == "东侧支护裂缝"
    assert order["rectify_requirement"] == "加固后现场复核"
    assert "无法判断" not in order["problem_location"]
    assert "纯色背景" not in order["problem_location"]


def test_confirm_invalid_ai_requires_manual_location(client, auth_headers, png_bytes):
    """P0-4：无效判别且无手填问题定位、无备注 → 1013 必填拦截（不建空壳工单）"""
    record_id, _ = _invalid_ai_record(client, auth_headers, png_bytes)
    r = client.post(f"/api/inspection-records/{record_id}/confirm",
                    json={"human_verdict": "abnormal",
                          "order_draft": {"assignee_id": 4, "problem_location": "  "}},
                    headers=auth_headers(2))
    assert r.json()["code"] == 1013
    # 记录未被确认，补手填后可重新提交成功
    r2 = client.post(f"/api/inspection-records/{record_id}/confirm",
                     json={"human_verdict": "abnormal",
                           "order_draft": {"assignee_id": 4,
                                           "problem_location": "东侧支护第二道支撑处裂缝",
                                           "rectify_requirement": "24 小时内加固复核", "basis": "经人工复核的依据"}},
                     headers=auth_headers(2))
    assert r2.json()["code"] == 0, r2.text
    order = r2.json()["data"]["order"]
    assert order["problem_location"] == "东侧支护第二道支撑处裂缝"
    assert order["rectify_requirement"] == "24 小时内加固复核"
