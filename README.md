# 工程现场智能巡查助手

系统由 FastAPI 后端、Vue 管理端和微信原生小程序组成，提供巡查计划、现场证据、AI 辅助分析、人工确认及整改复核闭环。

## 本地运行

环境要求：Python 3.12 或更新版本、Node.js 22 或更新版本，安装时加入 PATH。

1. 首次使用或依赖损坏时，双击 `scripts/setup.bat`。它会先停止本项目服务，再创建虚拟环境、安装后端依赖、执行 `npm.cmd ci` 并验证管理端构建。看到 `Installation verified` 才表示安装成功。
2. 双击 `scripts/start-all.bat`。看到“服务已就绪”后打开 http://127.0.0.1:15173 。日常启动只需这一步，不必重复安装。
3. 关闭服务时双击 `scripts/stop-all.bat`。关闭网页不会结束后台服务；重新安装依赖前必须先停止服务，推荐直接运行会自动停止服务的 setup.bat。

配置文件 backend/.env 只在缺失时从模板创建，已有配置、账号和业务数据会保留。全新演示数据库的管理员用户名为 admin；若未配置 DEV_SEED_PASSWORD，首次启动生成的密码保存在 backend/data/seed_credentials.txt。已有数据库继续使用原密码。

手动安装时请先运行 stop-all.bat，再在 admin-web 执行 `npm.cmd ci`。Windows 的 EPERM/unlink/rollup.node 通常表示仍有 Vite 进程占用文件，并不意味着必须使用管理员权限。安装中断后应完整重装，不能直接启动残缺的 node_modules。

启动程序先执行数据库迁移，校验后端身份并等待管理端代理就绪。后端文档位于 http://127.0.0.1:18090/docs 。启动失败详情在 `.runtime/backend.log` 或 `.runtime/admin.log`。已完整启动时重复点击会提示已有服务，避免启动重复进程。

微信开发者工具导入 miniprogram。模拟器本地接口为 http://127.0.0.1:18090；真机需要把 develop 地址改为电脑局域网地址。正式发布在 miniprogram/config/endpoints.js 中选择 cloudrun 或 https，填写对应环境和服务配置。

## 配置与部署

生产环境设置 APP_ENV=prod、强随机 JWT_SECRET、PostgreSQL 或 MySQL、Redis、明确的 HTTPS 域名和微信 WX_APPID/WX_SECRET。禁用开发登录。密钥仅放本地环境文件或部署平台的密钥配置中。

自建部署参考 docker-compose.yml 与 .env.production.example，配置证书后启动。云托管通过 `scripts/package-cloudrun.ps1` 生成代码包。生产 AI 默认关闭；完成现场样本评估并配置有效 AI_EVALUATION_REPORT 后才启用 AI_PRODUCTION_ENABLED。

升级前备份数据库及附件存储，再执行 `alembic upgrade head`。backend/migrations 是运行所需的数据库版本链，必须完整保留。备份恢复工具见 backend/scripts/backup_restore.py；数据库原生备份与恢复应在隔离环境演练后再用于正式环境。

### AI 供应商、异步 worker 与租户限速

- **供应商可配置**：通过 `AI_API_KEY`（回退 `DASHSCOPE_API_KEY`）、`AI_PROVIDER_BASE_URL`（OpenAI 兼容协议地址，默认 DashScope）、`AI_VISION_MODEL`（默认 `qwen3-vl-flash`）、`AI_TEXT_MODEL`（默认 `qwen-turbo`）、`AI_TIMEOUT` 自行切换供应商与模型，不再与单一供应商绑定。
- **异步队列 + 独立 worker**：AI 判别已入队异步执行。设 `AI_WORKER_ENABLED=true` 时，API 进程不再轮询队列，改由独立 worker 进程消费——docker-compose 已内置同镜像的 `worker` 服务（`SERVICE_ROLE=worker`）；单机/云托管单服务形态可保持 `false` 由调度器内置消费。
- **租户级限速**：`AI_TENANT_RATE`（令牌/秒，默认 60，置 0 关闭）与 `AI_TENANT_BURST`（突发上限，默认 120）构成租户级令牌桶；超限的请求自动降级为 mock 返回，避免成本失控。

### 安全加固

- **上传双重验证**：后端先做 MIME 白名单校验，再用 magic bytes 交叉验证真实类型，防止文件伪装。
- **对象私有 + 短时效签名**：S3/MinIO 对象强制 `ACL=private`，访问统一走 Pre-signed URL，有效期上限 15 分钟（`min(SIGNED_URL_TTL_SECONDS, 900)`），不依赖桶级公开权限。
- **通知渲染防护**：模板仅允许简单字段名占位符渲染（防注入），渲染前对全部占位符做存在性断言，失败时发送降级文本并记录错误告警。

## 质量验证

执行 `scripts/verify.ps1`，覆盖后端迁移与回归、小程序检查、管理端测试和构建。测试数据使用隔离临时数据库。CI 同时覆盖 PostgreSQL、MySQL 和 SQLite。正式上线仍需真实微信、短信、AI 样本效果和灾备演练验收。

目录：backend 为后端及迁移，admin-web 为管理端，miniprogram 为小程序，scripts 为启动/验证/打包工具，evaluation 为现场 AI 验收报告位置，.github 为持续集成配置。业务数据库及附件保存在 backend/data、backend/storage、backend/uploads。
