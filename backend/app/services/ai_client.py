# AI 模型网关：唯一模型调用入口，real/mock 自动切换
import json
import logging
import time

from ..config import settings

logger = logging.getLogger(__name__)

# 供应商/模型均可通过配置切换（AI_PROVIDER_BASE_URL / AI_VISION_MODEL / AI_TEXT_MODEL），
# 默认兼容阿里云 DashScope OpenAI 协议；保留模块级别名供 analyze/recommend 引用。
DASHSCOPE_BASE_URL = settings.AI_PROVIDER_BASE_URL
VISION_MODEL = settings.AI_VISION_MODEL
TEXT_MODEL = settings.AI_TEXT_MODEL


class AIError(Exception):
    """AI 调用失败异常，上层捕获后降级"""


# Mock 判别结果三选一（总纲 7.4）
MOCK_ANALYZE_RESULTS = [
    {"has_defect": True, "defect_desc": "照片右侧支护结构可见一道斜向裂缝，长约 40cm，缝宽目测约 2mm，位于第二道支撑与冠梁连接处附近", "severity": "中度", "basis": "JGJ 59-2011《建筑施工安全检查标准》：支护结构不得出现明显变形、裂缝", "confidence": 0.72, "suggestion": "建议设置观测标记持续监测裂缝发展，并通知专业单位复核"},
    {"has_defect": False, "defect_desc": "支护结构整体外观完好，未见明显变形、裂缝及渗漏现象，构件连接部位状态正常", "severity": "无", "basis": "JGJ 59-2011《建筑施工安全检查标准》表 3.13：支护结构无明显变形、裂缝", "confidence": 0.81, "suggestion": "维持常规巡查频次"},
    {"has_defect": True, "defect_desc": "临边防护栏杆缺失一段，长度约 3 米，该部位距坑边较近，存在人员坠落风险", "severity": "严重", "basis": "JGJ 59-2011：基坑临边应设置连续封闭防护栏杆", "confidence": 0.88, "suggestion": "立即封闭该区域通道，24 小时内补装防护栏杆"},
]


def image_message(b64_str: str) -> dict:
    """构造 OpenAI 兼容多模态图片消息块"""
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_str}"}}


class AIClient:
    """模型网关单例：mock 模式绝不实例化 openai client"""

    def __init__(self):
        self._api_key = settings.provider_api_key
        self._client = None  # 懒加载，仅 real 模式创建

    @property
    def mode(self) -> str:
        return "real" if self._api_key else "mock"

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=self._api_key, base_url=settings.AI_PROVIDER_BASE_URL,
                                  timeout=settings.AI_TIMEOUT, max_retries=0)
        return self._client

    def chat(self, messages: list, model: str = VISION_MODEL, purpose: str = "analyze",
             record_id: int | None = None) -> dict:
        """统一调用入口 → {content, mode}"""
        if settings.is_prod and not settings.AI_PRODUCTION_ENABLED:
            raise AIError("AI 尚未通过生产效果验收")
        if settings.is_prod:
            from .ai_acceptance import ensure_runtime_acceptance
            from .ai_meter import current_scope
            try:
                scope = current_scope()
                if scope is None:
                    raise ValueError('缺少项目上下文')
                if purpose == 'analyze':
                    from ..database import SessionLocal
                    from ..models import InspectionRecord
                    with SessionLocal() as db:
                        record = db.get(InspectionRecord, record_id) if record_id is not None else None
                        if record is None or record.project_id != scope[0]:
                            raise ValueError('分析记录不属于计费项目')
                        # 带 item_id/version 的全量校验已涵盖上面的项目 + 用途检查，
                        # 因此 analyze 分支只调用这一次，避免重复读取并校验验收报告。
                        ensure_runtime_acceptance(scope[0], purpose, item_id=record.item_id,
                            version=(record.item_snapshot or {}).get('version'))
                else:
                    ensure_runtime_acceptance(scope[0], purpose)
                expected_model = VISION_MODEL if purpose == 'analyze' else TEXT_MODEL
                if model != expected_model:
                    raise ValueError('模型不属于已验收版本')
            except (ValueError, OSError, KeyError, TypeError) as exc:
                raise AIError(f'AI运行期准入未通过：{exc}') from exc
        start = time.time()
        if self.mode == "mock":
            content = self._mock_content(purpose, record_id)
            logger.info("AI 调用 purpose=%s mode=mock 耗时=%.2fs 成功", purpose, time.time() - start)
            return {"content": content, "mode": "mock"}
        # real：先过租户令牌桶（超限降级 mock，保证可用性）；再异常重试 1 次，仍失败抛 AIError
        last_err = None
        from .ai_meter import reserve, finish, tenant_rate_allowed
        if settings.AI_TENANT_RATE > 0:
            allowed, retry_after = tenant_rate_allowed()
            if not allowed:
                content = self._mock_content(purpose, record_id)
                logger.warning("AI 调用 purpose=%s 触发租户令牌桶限速，降级 mock（约 %.0fs 后恢复）",
                               purpose, retry_after)
                return {"content": content, "mode": "mock", "degraded": "rate_limited"}
        for attempt in range(2):
            usage_id = reserve(purpose, model)
            try:
                resp = self._get_client().chat.completions.create(model=model, messages=messages)
                finish(usage_id, resp)
                content = resp.choices[0].message.content or ""
                logger.info("AI 调用 purpose=%s mode=real 耗时=%.2fs 成功", purpose, time.time() - start)
                return {"content": content, "mode": "real"}
            except Exception as e:  # noqa: BLE001 网络/API 异常统一重试
                finish(usage_id)
                last_err = e
                logger.warning("AI 调用 purpose=%s mode=real 第 %d 次失败: %s", purpose, attempt + 1, e)
        logger.error("AI 调用 purpose=%s mode=real 最终失败", purpose)
        raise AIError(f"AI 调用失败: {last_err}")

    def _mock_content(self, purpose: str, record_id: int | None) -> str:
        if purpose == "analyze":
            idx = (record_id or 0) % 3  # 基于 record_id 轮换，同记录结果稳定
            return json.dumps(MOCK_ANALYZE_RESULTS[idx], ensure_ascii=False)
        # draft/recommend 的 mock 逻辑在业务服务层直接处理，此处返回空 JSON 占位
        return "{}"


# 模块级单例
ai_client = AIClient()
