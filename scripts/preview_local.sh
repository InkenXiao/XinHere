#!/usr/bin/env bash
# 本地预览脚本：解决 WSL2 NAT 下 Windows 浏览器无法直达 vite dev(5173) 的问题。
# 做法：vite build 出 dist，用 nginx:alpine 容器把静态页发布到 8096（docker 发布端口 Windows 可达）。
#
# 用法（在 WSL 中执行）：
#   bash scripts/preview_local.sh                 # 仅前端，配合 ?mock=1 走前端 mock 数据
#   WITH_BACKEND=1 bash scripts/preview_local.sh  # 同时启动后端 uvicorn:8100，真实数据
#   PORT=9000 bash scripts/preview_local.sh       # 换端口
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FE="$ROOT/apps/frontend"
BE="$ROOT/apps/backend"
NODE_BIN="/root/.trae-cn-server/bin/stable-2c00292c18e2323abdb829d53fcd2d408a14796e-debian10"
PORT="${PORT:-8096}"
NAME="xinhere-web-preview"
CONF_DIR="$ROOT/deploy/.preview"

echo "==> 1/4 构建前端（tsc + vite build）"
export PATH="$NODE_BIN:$PATH"
cd "$FE"
./node_modules/.bin/tsc --noEmit
./node_modules/.bin/vite build

echo "==> 2/4 生成运行时 config.js（本地预览用空配置，前端回退默认值；mock 由 URL ?mock=1 控制）"
printf 'window.__ENV__ = {};\n' > dist/config.js

echo "==> 3/4 准备 nginx 配置"
mkdir -p "$CONF_DIR"
if [ "${WITH_BACKEND:-0}" = "1" ]; then
  cat > "$CONF_DIR/nginx.conf" <<'NGX'
server {
    listen 80;
    root /usr/share/nginx/html;
    index index.html;
    location /api/ {
        proxy_pass http://host.docker.internal:8100;
        proxy_buffering off;
        proxy_read_timeout 3600s;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
    location / { try_files $uri $uri/ /index.html; }
}
NGX
else
  cat > "$CONF_DIR/nginx.conf" <<'NGX'
server {
    listen 80;
    root /usr/share/nginx/html;
    index index.html;
    location / { try_files $uri $uri/ /index.html; }
}
NGX
fi

echo "==> 4/4 启动预览容器 $NAME（端口 $PORT）"
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" \
  --add-host=host.docker.internal:host-gateway \
  -p "${PORT}:80" \
  -v "$FE/dist:/usr/share/nginx/html:ro" \
  -v "$CONF_DIR/nginx.conf:/etc/nginx/conf.d/default.conf:ro" \
  nginx:alpine >/dev/null

if [ "${WITH_BACKEND:-0}" = "1" ]; then
  echo "==> 启动后端 uvicorn :8100（DB 走 localhost:11000）"
  cd "$BE"
  export POSTGRES_HOST=localhost POSTGRES_PORT=11000
  nohup .venv/bin/python -m alembic upgrade head > /tmp/xinhere-alembic.log 2>&1 || true
  nohup .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8100 > /tmp/xinhere-backend.log 2>&1 &
  sleep 2
  tail -5 /tmp/xinhere-backend.log || true
  echo ""
  echo "完成！Windows 浏览器打开: http://localhost:${PORT}/        （真实数据，后端日志 /tmp/xinhere-backend.log）"
else
  echo ""
  echo "完成！Windows 浏览器打开: http://localhost:${PORT}/?mock=1  （前端 mock 数据）"
fi
