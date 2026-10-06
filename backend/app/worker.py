# 独立 AI worker 进程：消费 AiJob 队列（异步推理独立服务，与 API 进程解耦）。
# 用法：python -m app.worker（配合 AI_WORKER_ENABLED=true 时 API 进程不再轮询 ai_jobs）。
# 多实例安全：process_ai_jobs 内部通过 AiJob 行级租约（token + lease_until）CAS 抢占，天然支持横向扩展。
import logging
import time

from .config import settings
from .database import SessionLocal
from .observability import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 3.0


def run_worker() -> None:
    # 数据库迁移由容器 entrypoint 完成；此处仅做幂等种子（通知模板等基础数据）。
    from .seed import init_seed

    db = SessionLocal()
    try:
        init_seed(db)
    finally:
        db.close()

    from .services.operations import process_ai_jobs

    logger.info("AI worker 启动，轮询间隔 %.1fs（APP_ENV=%s AI_MODE=%s 供应商=%s）",
                POLL_INTERVAL_SECONDS, settings.APP_ENV, settings.AI_MODE, settings.AI_PROVIDER_BASE_URL)
    while True:
        try:
            handled = process_ai_jobs()
            if handled:
                logger.info("AI worker 本轮处理 AI 任务 %d 条", handled)
        except Exception as exc:  # noqa: BLE001  单轮异常不得终止 worker
            logger.exception("AI worker 轮询异常: %s", exc)
        time.sleep(POLL_INTERVAL_SECONDS)


def main() -> None:
    run_worker()


if __name__ == "__main__":
    main()