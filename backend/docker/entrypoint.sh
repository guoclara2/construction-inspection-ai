#!/bin/sh
# 容器入口：先执行数据库迁移到最新，再启动应用（消灭自动建表 D1）
# 监听端口由 PORT 环境变量控制：微信云托管默认 8080（与控制台版本配置一致）；
# 自建 docker-compose 部署在 compose 中显式传 PORT=8000
set -e

echo "[entrypoint] 执行数据库迁移 alembic upgrade head ..."
alembic upgrade head

echo "[entrypoint] 启动 uvicorn（端口 ${PORT:-8080}）..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8080}"
