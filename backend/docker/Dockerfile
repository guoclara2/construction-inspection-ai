# 多阶段构建：先装依赖，再以非 root 用户运行；启动前执行 alembic upgrade head
FROM python:3.12-slim AS builder
WORKDIR /app
COPY requirements.txt constraints.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
WORKDIR /app

# 运行期系统依赖（Pillow/psycopg 运行库）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 libjpeg62-turbo zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local
COPY app ./app
COPY migrations ./migrations
COPY scripts/bootstrap_admin.py scripts/evaluate_ai.py scripts/backup_restore.py ./scripts/
COPY alembic.ini ./
COPY docker/entrypoint.sh ./docker/entrypoint.sh
RUN chmod +x ./docker/entrypoint.sh

# 非 root 用户运行，降低容器逃逸风险
RUN useradd -m appuser \
    && mkdir -p /app/storage /app/uploads /app/data \
    && chown -R appuser:appuser /app
USER appuser

ENV APP_ENV=prod \
    PYTHONUNBUFFERED=1

EXPOSE 8000
ENTRYPOINT ["./docker/entrypoint.sh"]
