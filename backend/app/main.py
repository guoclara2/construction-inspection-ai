# FastAPI 入口：迁移自检 + 种子、CORS、结构化日志、trace 中间件、健康探针
# P04 起不再挂载 /uploads 公开静态目录：附件一律走 /api/attachments/{id}/file 鉴权下载
# 云托管单服务形态（WEB_DIST_DIR 配置时）：后端直接托管 admin-web 构建产物（SPA 回退到 index.html）
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from .auth import router as auth_router
from .config import DATA_DIR, settings
from .database import SessionLocal, engine
from .migration_check import migration_status
from .observability import TraceMiddleware, setup_logging
from .seed import init_admin_from_env, init_demo_data, init_seed
from .utils import register_exception_handlers
from . import models  # noqa: F401  确保模型注册到 Base.metadata

setup_logging()
logger = logging.getLogger(__name__)

os.makedirs(DATA_DIR, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动自检：不再自动建表；改为校验 Alembic 版本是否为最新（消灭 D1）
    status, current, head = migration_status()
    if status != "head":
        msg = (f"数据库迁移未升级到最新（current={current}, head={head}），"
               f"请先执行：alembic upgrade head")
        if settings.is_prod:
            logger.error("[启动拒绝] %s", msg)
            raise SystemExit(1)
        logger.warning("[启动告警] %s", msg)

    # 基础种子 + 演示种子（幂等）；表由迁移或测试夹具建立
    db = SessionLocal()
    try:
        init_seed(db)
        # 云托管首启：环境变量初始化首个管理员（空库一次性；详见 seed.init_admin_from_env）
        init_admin_from_env(db)
        if os.environ.get("INIT_DEMO_DATA", "1") != "0":
            init_demo_data(db)
    finally:
        db.close()

    # 定时任务（P06 §2.2）：APScheduler 后台调度，SCHEDULER_ENABLED=false 时不启动
    scheduler = None
    if settings.SCHEDULER_ENABLED:
        from .services.scheduler import build_scheduler

        scheduler = build_scheduler()
        scheduler.start()
        jobs = ", ".join(f"{j.id}@{j.trigger}" for j in scheduler.get_jobs())
        logger.info("定时任务已启动（%d 个）：%s", len(scheduler.get_jobs()), jobs)
    else:
        logger.warning("SCHEDULER_ENABLED=false，定时任务未启动（超期扫描/通知重试均不执行）")

    logger.info("服务启动完成，APP_ENV=%s，AI 模式=%s，迁移状态=%s", settings.APP_ENV, settings.AI_MODE, status)
    app.state.scheduler = scheduler
    yield
    if scheduler is not None:
        scheduler.shutdown(wait=False)
        logger.info("定时任务已停止")


app = FastAPI(title="工程现场智能巡查助手", lifespan=lifespan)
register_exception_handlers(app)

# trace 中间件放在最外层，保证所有响应都带 X-Trace-Id
app.add_middleware(TraceMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,   # 环境化，禁止硬编码（消灭 J2）
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _check_database() -> str:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning("健康检查：数据库不可用 %s", exc)
        return "error"


def _check_redis() -> str:
    if not settings.REDIS_URL.strip():
        return "skipped"
    try:
        import redis  # 延迟导入，未配置 Redis 时不影响启动

        client = redis.from_url(settings.REDIS_URL, socket_connect_timeout=1)
        client.ping()
        return "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning("健康检查：Redis 不可用 %s", exc)
        return "error"


def _check_storage() -> str:
    """存储适配器探活：经统一接口写入/读回/删除探针对象（local 与 s3 通用）"""
    try:
        from .services.storage import get_storage

        storage = get_storage()
        probe_key = "_health/" + __import__("uuid").uuid4().hex + ".txt"
        storage.put(probe_key, b"ok", "text/plain")
        healthy = storage.get(probe_key) == b"ok"
        storage.delete(probe_key)
        return "ok" if healthy else "error"
    except Exception as exc:  # noqa: BLE001
        logger.warning("健康检查：存储不可用 %s", exc)
        return "error"


@app.get("/api/health")
@app.get("/health")  # P3-1：运维探活惯例路径别名，与 /api/health 同一 handler
def health(response: Response):
    """就绪探针：数据库/Redis/存储/迁移四项连通性；任一非 ok 则 status=degraded，HTTP 503"""
    checks = {
        "database": _check_database(),
        "redis": _check_redis(),
        "storage": _check_storage(),
        "migration": "head" if migration_status()[0] == "head" else "behind",
        "scheduler": "ok" if getattr(getattr(app.state,"scheduler",None),"running",False) else ("error" if settings.is_prod else "skipped"),
    }
    healthy = checks["database"] == "ok" and checks["storage"] == "ok" and checks["migration"] == "head" \
        and checks["redis"] in ("ok", "skipped") and checks["scheduler"] in ("ok", "skipped")
    response.status_code = 200 if healthy else 503
    return {"code": 0, "msg": "ok", "data": {
        "status": "ok" if healthy else "degraded",
        "app_env": settings.APP_ENV,
        "version": os.environ.get("GIT_SHA", "dev"),
        "ai_mode": settings.AI_MODE,
        "checks": checks,
    }}


@app.get("/api/health/live")
def health_live():
    """存活探针：进程存活即返回 ok，不依赖外部组件"""
    return {"code": 0, "msg": "ok", "data": {"status": "ok", "service": "site-inspection"}}


app.include_router(auth_router)

# 开发态登录路由：仅 dev + 显式开关时注册（P0-2 三重防护之①②）；
# 来源 IP 白名单校验在路由内逐请求执行（routers/dev_auth.py 之③），任一不满足返回 404
if settings.APP_ENV == "dev" and settings.DEV_LOGIN_ENABLED:
    from .routers import dev_auth
    app.include_router(dev_auth.router)
    logger.warning("开发模式登录已启用（/api/auth/dev-login），严禁用于生产环境")

from .routers import admin, attachments, inspection, messages, orders, projects, users  # noqa: E402

app.include_router(projects.router)
app.include_router(inspection.router)
app.include_router(admin.router)
app.include_router(orders.router)
app.include_router(messages.router)
app.include_router(attachments.router)
app.include_router(users.router)

from .routers import operations
app.include_router(operations.router)

from .routers import reports
app.include_router(reports.router)


# ---------- admin-web 同源静态托管（云托管单服务形态）----------
# SPA 回退：非 /api、非静态资源路径统一回 index.html（vue-router history 模式刷新可用）；
# /api 前缀 404 保持 404（避免把接口错误吞成 HTML）。挂载在全部路由之后，不影响 API 匹配。
class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            # StaticFiles 会把 URL 路径归一化为「无前导斜杠 + 系统分隔符」：
            # Linux 下为 "api/..."，Windows 下为 "api\\..."，故先统一成 "/" 再判断前缀。
            if exc.status_code == 404 and not path.replace("\\", "/").startswith("api/"):
                return await super().get_response("index.html", scope)
            raise


if settings.web_dist_root:
    app.mount("/", SPAStaticFiles(directory=settings.web_dist_root, html=True), name="admin-web")
    logger.info("admin-web 静态托管已启用：%s（CORS 可留空，前后端同源）", settings.web_dist_root)
