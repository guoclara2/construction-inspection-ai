# 配置模块：分环境读取 .env、启动自检、判定 AI 模式（总纲第 9 章为唯一契约源）
import logging
import os
import sys

from pydantic_settings import BaseSettings
from pydantic import Field

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")

logger = logging.getLogger(__name__)

# JWT_SECRET 的示例/占位值集合：prod 下命中任一即视为不安全
_INSECURE_SECRETS = {"dev-secret-change-me", "change-me", "secret", "changeme"}


class Settings(BaseSettings):
    # —— 运行环境 ——
    APP_ENV: str = "dev"                       # dev/test/prod；prod 启用全部生产校验
    APP_TIMEZONE: str = "Asia/Shanghai"        # 接口输出时区

    # —— 数据库 / 缓存 ——
    DATABASE_URL: str = "sqlite:///./data/app.db"   # prod 必须 postgresql+psycopg://
    REDIS_URL: str = ""                         # 空则限流/幂等降级为进程内

    # —— 令牌 ——
    JWT_SECRET: str = "dev-secret-change-me"    # prod 下等于示例值或 <32 位则拒绝启动
    ACCESS_TOKEN_MINUTES: int = 120
    REFRESH_TOKEN_DAYS: int = 14

    # —— 开发登录（仅 dev 可开）——
    DEV_LOGIN_ENABLED: bool = False
    DEV_LOGIN_ALLOWED_IPS: str = "127.0.0.1,::1"  # dev-login 允许的请求来源 IP 白名单（逗号分隔，P0-2）
    DEV_SEED_PASSWORD: str = ""                 # 种子账号密码（仅 dev/test 使用）；空则随机生成并打印到日志

    # —— 文件存储 ——
    STORAGE_BACKEND: str = "local"              # local/s3/wxcloud（微信云托管对象存储）
    STORAGE_LOCAL_DIR: str = "./storage"
    S3_ENDPOINT: str = ""
    S3_BUCKET: str = ""
    S3_AK: str = ""
    S3_SK: str = ""
    S3_REGION: str = ""
    # wxcloud 专用（云托管控制台-设置-环境设置 看 WX_ENV_ID；对象存储-存储配置 看 WX_COS_BUCKET）
    WX_ENV_ID: str = ""
    WX_COS_BUCKET: str = ""
    WX_COS_REGION: str = "ap-shanghai"
    # 云托管「开放接口服务」免鉴权：容器内经 http://api.weixin.qq.com 调微信接口无需 access_token
    # （需在云托管控制台-云调用 开启开关并配置接口权限）；true 时 wxcloud 存储不再依赖 WX_APPID/WX_SECRET
    WX_CLOUD_CALL: bool = False
    SIGNED_URL_TTL_SECONDS: int = 300
    WATERMARK_ENABLED: bool = True
    MAX_UPLOAD_MB: int = 20
    EXIF_MAX_SKEW_MINUTES: int = 120     # 拍摄时间与接收时间偏差阈值，超过标 suspicious

    # —— AI ——
    DASHSCOPE_API_KEY: str = ""                 # 兼容旧配置：等于 AI_API_KEY 的后备（prod 下必须其一非空）
    AI_API_KEY: str = ""                        # AI 供应商 API Key（可配置，优先于 DASHSCOPE_API_KEY）
    AI_PROVIDER_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"  # OpenAI 兼容协议地址
    AI_VISION_MODEL: str = "qwen3-vl-flash"     # 图像判别模型（可配置）
    AI_TEXT_MODEL: str = "qwen-turbo"           # 文本/推荐模型（可配置）
    AI_TIMEOUT: float = Field(default=30.0, ge=1, le=300)  # 单次模型调用超时（秒）
    AI_PRODUCTION_ENABLED: bool = False
    AI_EVALUATION_REPORT: str = ""
    AI_REVIEW_THRESHOLD: float = Field(default=0.6, ge=0, le=1)
    AI_USER_DAILY_QUOTA: int = Field(default=100,ge=1,le=100000)
    AI_PROJECT_CONCURRENCY: int = Field(default=2,ge=1,le=32)
    AI_PRICES: dict = {}  # model -> {input, output}: CNY per million tokens
    AI_DAILY_QUOTA: int = Field(default=500,ge=1,le=1000000)
    AI_RECALL_ALERT: float = 0.7
    # —— AI 异步 worker 与租户级限速 ——
    AI_WORKER_ENABLED: bool = False             # 独立 worker 进程开关；true 时 API 进程不再轮询 AI 队列
    AI_TENANT_RATE: float = Field(default=60.0, ge=0)   # 租户级令牌桶补充速率（令牌/秒）；0 表示关闭限速
    AI_TENANT_BURST: int = Field(default=120, ge=1)      # 租户级令牌桶容量（突发上限）

    # —— 微信 / 短信 ——
    WX_APPID: str = ""
    WX_SECRET: str = ""
    WX_SUBSCRIBE_TEMPLATE_ORDER: str = ""
    WX_SUBSCRIBE_TEMPLATE_OVERDUE: str = ""
    SMS_PROVIDER: str = ""
    SMS_AK: str = ""
    SMS_SK: str = ""
    SMS_SIGN: str = ""
    SMS_TEMPLATE_CODE: str = ""             # 验证码短信模板 ID（通知类模板 ID 存 notification_templates）
    SCHEDULER_ENABLED: bool = True          # 定时任务总开关；测试/单机排查可置 false（P06）

    # —— 云托管部署（微信云托管单服务形态）——
    # admin-web 静态托管目录（容器内相对 backend 目录；留空=不托管，走独立前端部署）
    WEB_DIST_DIR: str = ""
    # 首个管理员环境变量初始化（prod 空库时一次性执行；见 seed.init_admin_from_env）
    ADMIN_INIT_USERNAME: str = ""
    ADMIN_INIT_PASSWORD: str = ""
    ADMIN_INIT_ORG: str = ""
    ADMIN_INIT_PROJECT: str = ""
    # 云托管单实例试点无 Redis 时允许进程内降级（限流/幂等仅进程内生效，扩容前必须接入 Redis）
    REDIS_ALLOWED_EMPTY: bool = False

    # —— 网络 / 日志 ——
    CORS_ORIGINS: str = "http://localhost:5173"  # 逗号分隔
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"                      # prod 用 json 结构化日志
    SENTRY_DSN: str = ""

    model_config = {"env_file": os.path.join(BASE_DIR, ".env"), "extra": "ignore"}

    @property
    def provider_api_key(self) -> str:
        """AI 供应商 API Key：优先 AI_API_KEY，回退 DASHSCOPE_API_KEY（向后兼容）"""
        return self.AI_API_KEY.strip() or self.DASHSCOPE_API_KEY.strip()

    @property
    def AI_MODE(self) -> str:
        return "manual" if self.is_prod and not self.AI_PRODUCTION_ENABLED else ("real" if self.provider_api_key else "mock")

    @property
    def is_prod(self) -> bool:
        return self.APP_ENV.strip().lower() == "prod"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def dev_login_allowed_ips(self) -> set[str]:
        """dev-login 来源 IP 白名单集合（P0-2）"""
        return {ip.strip() for ip in self.DEV_LOGIN_ALLOWED_IPS.split(",") if ip.strip()}

    @property
    def storage_local_root(self) -> str:
        """本地存储根目录的绝对路径（web 根之外）：相对路径基于 backend 目录解析"""
        raw = self.STORAGE_LOCAL_DIR.strip()
        if os.path.isabs(raw):
            return raw
        return os.path.normpath(os.path.join(BASE_DIR, raw.lstrip("./").replace("/", os.sep)))

    @property
    def web_dist_root(self) -> str:
        """admin-web 静态托管目录绝对路径；未配置或目录不存在时返回空串（不托管）"""
        raw = self.WEB_DIST_DIR.strip()
        if not raw:
            return ""
        root = raw if os.path.isabs(raw) else os.path.normpath(
            os.path.join(BASE_DIR, raw.lstrip("./").replace("/", os.sep)))
        return root if os.path.isdir(root) else ""

    @property
    def db_url(self) -> str:
        # SQLite 相对路径统一转为基于 backend 目录的绝对路径，保证任意 cwd 启动正确
        if self.DATABASE_URL.startswith("sqlite:///./"):
            return "sqlite:///" + os.path.join(BASE_DIR, self.DATABASE_URL[len("sqlite:///./"):]).replace("\\", "/")
        return self.DATABASE_URL


def _collect_prod_problems(s: Settings) -> list[str]:
    """收集 prod 环境下不满足的生产准入项（Alembic head 校验在启动 lifespan 内完成）"""
    problems: list[str] = []
    if len(s.JWT_SECRET) < 32 or s.JWT_SECRET.strip() in _INSECURE_SECRETS:
        problems.append("JWT_SECRET 不安全：生产环境要求长度≥32 且不得为示例值")
    if not s.DATABASE_URL.startswith(("postgresql", "mysql+pymysql")):
        problems.append("DATABASE_URL 必须为 PostgreSQL（postgresql+psycopg://...）或 MySQL（mysql+pymysql://...，微信云托管）")
    if s.DEV_LOGIN_ENABLED:
        problems.append("DEV_LOGIN_ENABLED 生产环境必须为 false")
    if not (s.WX_APPID.strip() and s.WX_SECRET.strip()):
        problems.append("微信登录必须配置 WX_APPID/WX_SECRET，服务端需校验 wx.login code")
    if s.AI_PRODUCTION_ENABLED and not s.provider_api_key:
        problems.append("AI_API_KEY/DASHSCOPE_API_KEY 生产环境不得为空（禁止 mock 模式）")
    if any(h in s.CORS_ORIGINS for h in ("localhost", "127.0.0.1")):
        problems.append("CORS_ORIGINS 生产环境不得包含 localhost/127.0.0.1")
    if s.STORAGE_BACKEND == "s3" and not all([s.S3_ENDPOINT, s.S3_BUCKET, s.S3_AK, s.S3_SK, s.S3_REGION]):
        problems.append("STORAGE_BACKEND=s3 时 S3_ENDPOINT/S3_BUCKET/S3_AK/S3_SK/S3_REGION 五项必须齐全")
    if s.STORAGE_BACKEND == "wxcloud" and not all([s.WX_ENV_ID, s.WX_COS_BUCKET]):
        problems.append("STORAGE_BACKEND=wxcloud 时 WX_ENV_ID/WX_COS_BUCKET 必须配置（云托管控制台查看）")
    if s.STORAGE_BACKEND == "wxcloud" and not (s.WX_CLOUD_CALL or (s.WX_APPID.strip() and s.WX_SECRET.strip())):
        problems.append("wxcloud 存储需二选一：云托管开启 WX_CLOUD_CALL=true（开放接口服务免鉴权）或配置 WX_APPID/WX_SECRET")
    if not s.REDIS_URL.strip() and not s.REDIS_ALLOWED_EMPTY:
        problems.append("生产环境必须配置 REDIS_URL（云托管单实例试点可置 REDIS_ALLOWED_EMPTY=true 降级进程内）")
    # admin-web 同源托管（云托管单服务形态）时无需 CORS，允许留空；非空时必须为明确的 HTTPS 地址
    if s.cors_origins_list and any(not o.startswith("https://") or "*" in o for o in s.cors_origins_list):
        problems.append("生产 CORS 必须为明确的 HTTPS 地址（同源部署可留空）")
    if s.AI_PRODUCTION_ENABLED:
        from .services.ai_acceptance import validate_report
        try:
            validate_report(s.AI_EVALUATION_REPORT, review_threshold=s.AI_REVIEW_THRESHOLD)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            problems.append(f"AI 效果准入未通过：{exc}")
    return problems


def validate_production_config(s: Settings) -> None:
    """启动自检：prod 下任一不满足即打印中文原因并退出；dev/test 降级为 WARNING。

    注：Alembic 当前版本==head 的校验依赖数据库连接，放在 app.main 的 lifespan 中执行。
    """
    problems = _collect_prod_problems(s)
    if not problems:
        return
    if s.is_prod:
        sys.stderr.write("[生产配置自检失败] 拒绝启动，原因如下：\n")
        for p in problems:
            sys.stderr.write(f"  - {p}\n")
        sys.stderr.flush()
        sys.exit(1)
    else:
        for p in problems:
            logger.warning("[配置提示] %s（当前 APP_ENV=%s，不阻断启动）", p, s.APP_ENV)


settings = Settings()
# 模块加载即执行静态自检（prod 不合规直接退出，供 `python -c "from app.config import settings"` 探测）
validate_production_config(settings)
