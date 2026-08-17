#!/usr/bin/env bash
# ============================================================================
# XinHere 生产发布脚本（本机 docker compose）
# ----------------------------------------------------------------------------
# 设计目标：
#   1. 改代码 / 改环境变量 → 不重建镜像（源码与配置卷挂载，重启容器即生效）
#   2. 仅依赖变化（requirements.txt / package.json / package-lock.json）才重建镜像
#   3. 数据库结构变更自动执行 alembic upgrade head（幂等）
#   4. 前端 dist 在宿主构建后挂载；前端运行时配置（VITE_*）改后重启 frontend 即生效
# 用法：bash deploy/publish.sh
# ============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

COMPOSE="docker compose -f docker-compose.yml"
HASH_FILE=".deploy-hash"

log() { echo -e "\033[1;34m[xinhere-publish]\033[0m $*"; }
err() { echo -e "\033[1;31m[xinhere-publish][ERROR]\033[0m $*" >&2; }

# ---------- 1. 构建前端 dist（宿主；node_modules 缺失时先 npm ci） ----------
log "步骤 1/4：构建前端 dist ..."
if [ ! -d apps/frontend/node_modules ]; then
  log "  首次构建，执行 npm ci（较慢）..."
  (cd apps/frontend && npm ci)
fi
(cd apps/frontend && npm run build)
log "  前端产物已更新: apps/frontend/dist"

# ---------- 2. 依赖变化检测 → 仅依赖变化时重建镜像 ----------
log "步骤 2/4：检查依赖是否变化（决定是否重建镜像）..."
DEPS_HASH="$(cat apps/backend/requirements.txt apps/frontend/package.json apps/frontend/package-lock.json | md5sum | awk '{print $1}')"
if [ "$DEPS_HASH" != "$(cat "$HASH_FILE" 2>/dev/null || true)" ]; then
  log "  依赖发生变化，重建镜像（backend 仅重跑依赖层，frontend 为纯 nginx）..."
  $COMPOSE build
  echo "$DEPS_HASH" > "$HASH_FILE"
  log "  镜像重建完成"
else
  log "  依赖无变化，跳过镜像重建（改代码/改配置不重建镜像）"
fi

# ---------- 3. 数据库迁移（幂等） ----------
log "步骤 3/4：执行数据库迁移 alembic upgrade head ..."
$COMPOSE run --rm backend alembic upgrade head
log "  数据库迁移完成"

# ---------- 4. 启动 / 更新服务 ----------
log "步骤 4/4：启动服务 ..."
$COMPOSE up -d
log "发布完成。前端 http://localhost:8096 （API 经 nginx 反代 backend:8000）"
