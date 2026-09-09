#!/usr/bin/env bash
# XinHere 一键启动：build → up → 迁移 → 幂等 seed
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== docker compose build =="
docker compose build

echo "== docker compose up -d =="
docker compose up -d

echo "== 等待 backend 健康（超时 120s）=="
deadline=$((SECONDS + 120))
until docker compose exec -T backend curl -sf http://localhost:8196/healthz >/dev/null 2>&1; do
  if (( SECONDS >= deadline )); then
    echo "ERROR: backend 120s 内未就绪，当前状态：" >&2
    docker compose ps >&2
    docker compose logs --tail 50 backend >&2
    exit 1
  fi
  sleep 2
done
echo "backend 健康"

echo "== alembic upgrade head =="
docker compose exec -T backend python -m alembic upgrade head

echo "== python -m app.seed（幂等）=="
docker compose exec -T backend python -m app.seed

echo
echo "XinHere 已启动："
echo "  访问入口:  https://localhost:8096 （经外部 nginx https 反代 frontend 容器）"
echo "  API 调试:  https://localhost:8096/api/... （backend 不发布宿主端口，经 frontend nginx 反代 backend:8196）"
