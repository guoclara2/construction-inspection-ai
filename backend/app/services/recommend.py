# 事项推荐服务：结构化匹配 + 语义补充（real/mock）
import json
import logging
import time

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models import InspectionItem
from ..utils import loads_safe, now_utc
from .ai_client import TEXT_MODEL, AIError, ai_client

logger = logging.getLogger(__name__)

RISK_WEIGHT = {"high": 0, "medium": 1, "low": 2}
RISK_CN = {"high": "高风险", "medium": "中风险", "low": "低风险"}
MAX_RECOMMEND = 12

RECOMMEND_SYSTEM_PROMPT = """你是工程巡查助手。从下方检查项清单中筛选出与用户描述相关的检查事项。
仅输出 JSON，不要输出其他文字：
{"ids": [检查项id数组], "reasons": {"id": "推荐理由一句话"}}
要求：ids 中的 id 必须来自清单；与描述无关的不要选。"""


def _org_filter(org_id: int | None):
    """知识库组织过滤：本组织条目 + 平台内置模板（org_id IS NULL）"""
    from sqlalchemy import and_
    return and_(or_(InspectionItem.org_id == org_id, InspectionItem.org_id.is_(None)),
        or_(InspectionItem.effective_from.is_(None),InspectionItem.effective_from<=now_utc()),
        or_(InspectionItem.effective_until.is_(None),InspectionItem.effective_until>now_utc()))


def recommend_items(project_type: str, phase: str, text: str | None, db: Session,
                    org_id: int | None = None) -> list[dict]:
    """推荐主流程：结构化匹配 → 语义补充 → 合并去重（上限 12），全程按 org 隔离知识库"""
    # 1. 结构化匹配：类型+阶段 LIKE，风险等级排序
    structured = (
        db.query(InspectionItem)
        .filter(
            InspectionItem.enabled.is_(True),
            _org_filter(org_id),
            InspectionItem.applicable_types.like(f'%"{project_type}"%'),
            InspectionItem.applicable_phases.like(f'%"{phase}"%'),
        )
        .all()
    )
    structured.sort(key=lambda it: RISK_WEIGHT.get(it.risk_level, 9))
    result_ids = [it.id for it in structured]
    results = []
    for it in structured:
        results.append({
            "item": _item_dict(it),
            "reason": f"结构化匹配-{RISK_CN.get(it.risk_level, it.risk_level)}",
        })

    # 2. 语义补充
    if text and text.strip():
        text = text.strip()
        if ai_client.mode == "real":
            _supplement_by_model(text, db, result_ids, results, org_id)
        else:
            # mock：名称包含关键词者补充
            keyword_hits = (
                db.query(InspectionItem)
                .filter(InspectionItem.enabled.is_(True), _org_filter(org_id),
                        InspectionItem.name.like(f"%{text}%"))
                .all()
            )
            for it in keyword_hits:
                if it.id not in result_ids:
                    result_ids.append(it.id)
                    results.append({"item": _item_dict(it), "reason": "语义补充"})

    # 3. 上限 12 条
    return results[:MAX_RECOMMEND]


def _supplement_by_model(text: str, db: Session, result_ids: list, results: list, org_id: int | None = None):
    """real 模式：qwen-turbo 从启用检查项清单中筛选补充（按 org 隔离）"""
    # 结果集有界：本组织启用检查项全量清单（喂给模型的目录，知识库量级 < 数百）
    items = db.query(InspectionItem).filter(InspectionItem.enabled.is_(True), _org_filter(org_id)).all()
    from ..config import settings
    if settings.is_prod:
        from .ai_acceptance import ensure_runtime_acceptance
        from .ai_meter import current_scope
        try:
            scope = current_scope()
            policy = ensure_runtime_acceptance(scope[0] if scope else None, 'recommend')
            allowed = {(v['item_id'], v['version']) for v in policy['item_versions']}
            items = [item for item in items if (item.id, item.version) in allowed]
            if not items:
                return
        except (ValueError, OSError, KeyError, TypeError) as exc:
            logger.warning('推荐未通过AI准入，仅返回结构化结果：%s', exc)
            return
    catalog = "\n".join(f"{it.id}. {it.name}" for it in items)
    messages = [
        {"role": "system", "content": RECOMMEND_SYSTEM_PROMPT},
        {"role": "user", "content": f"【检查项清单】\n{catalog}\n\n【用户描述】{text}"},
    ]
    try:
        start = time.time()
        resp = ai_client.chat(messages, model=TEXT_MODEL, purpose="recommend")
        logger.info("AI 调用 purpose=recommend mode=real 耗时=%.2fs 成功", time.time() - start)
        from .analyze import parse_json_block
        data = parse_json_block(resp["content"])
        if not data or not isinstance(data.get("ids"), list):
            logger.warning("推荐语义补充解析失败")
            return
        item_map = {it.id: it for it in items}
        reasons = data.get("reasons") if isinstance(data.get("reasons"), dict) else {}
        for raw_id in data["ids"]:
            try:
                item_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if item_id in result_ids or item_id not in item_map:
                continue
            result_ids.append(item_id)
            results.append({"item": _item_dict(item_map[item_id]),
                            "reason": str(reasons.get(str(item_id), "语义补充")) or "语义补充"})
    except AIError as e:
        logger.error("推荐语义补充调用失败: %s", e)


def _item_dict(item: InspectionItem) -> dict:
    """检查项精简序列化（含解析后的数组字段）"""
    return {
        "id": item.id,
        "name": item.name,
        "check_points": loads_safe(item.check_points, []),
        "basis": item.basis,
        "defect_category": item.defect_category,
        "risk_level": item.risk_level,
        "applicable_types": loads_safe(item.applicable_types, []),
        "applicable_phases": loads_safe(item.applicable_phases, []),
    }
