#!/bin/sh
# nginx entrypoint 阶段脚本：把容器环境变量渲染为前端运行时配置 /usr/share/nginx/html/config.js
# 生效方式：修改 docker-compose 的 VITE_* 环境变量后 docker compose up -d 重启 frontend 即生效，
# 无需重新构建 dist / 重建镜像。
# 说明：envsubst 限定变量列表，未设置变量输出为空串（前端 config.ts 回退构建期默认值）。
set -eu

TPL="${XINHERE_CONFIG_TPL:-/etc/xinhere/config.js.template}"
OUT="${XINHERE_CONFIG_OUT:-/usr/share/nginx/html/config.js}"

if [ ! -f "$TPL" ]; then
  echo "[render-env] template not found: $TPL, skip"
  exit 0
fi

envsubst '$VITE_API_BASE $VITE_OPS_URL $VITE_KB_URL $VITE_COWORK_URL $VITE_MOCK' < "$TPL" > "$OUT"
echo "[render-env] wrote $OUT"
