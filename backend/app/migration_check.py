# 迁移状态自检：比较数据库当前 Alembic 版本与脚本 head（替代自动建表 create_all）
import logging
import os

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from .config import BASE_DIR
from .database import engine

logger = logging.getLogger(__name__)


def migration_status() -> tuple[str, str | None, str | None]:
    """返回 (status, current, head)。status ∈ {head, behind, unknown}。

    - head：数据库版本与脚本 head 一致
    - behind：不一致（含数据库尚未 stamp 的空库）
    - unknown：无法判定（如 alembic 配置缺失）
    """
    try:
        cfg = Config(os.path.join(BASE_DIR, "alembic.ini"))
        script = ScriptDirectory.from_config(cfg)
        head = script.get_current_head()
        with engine.connect() as conn:
            current = MigrationContext.configure(conn).get_current_revision()
        return ("head" if current == head else "behind", current, head)
    except Exception as exc:  # noqa: BLE001 迁移探测失败不应遮蔽真正错误
        logger.warning("Alembic 版本探测失败: %s", exc)
        return ("unknown", None, None)
