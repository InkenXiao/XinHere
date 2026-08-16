#!/usr/bin/env bash
# 停止 stack（不删卷、不删镜像）
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose down
echo "XinHere 已停止（数据卷保留）"
