#!/usr/bin/env bash
# E2E 入口（W4 填充 tests/e2e/）：检查 stack 健康 → pytest tests/e2e -v
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== 检查 stack 健康 =="
curl -sf http://127.0.0.1:8100/healthz >/dev/null || {
  echo "ERROR: backend 未就绪（http://127.0.0.1:8100/healthz），请先 bash scripts/start.sh" >&2
  exit 1
}
curl -sf http://localhost:8095/ >/dev/null || {
  echo "ERROR: frontend 未就绪（http://localhost:8095/），请先 bash scripts/start.sh" >&2
  exit 1
}
echo "stack 健康"

if [[ ! -d tests/e2e ]]; then
  echo "tests/e2e/ 尚未填充（W4 工作内容），友好跳过 E2E"
  exit 0
fi

PYTEST="apps/backend/.venv/bin/python -m pytest"
[[ -x apps/backend/.venv/bin/python ]] || PYTEST="python3 -m pytest"
$PYTEST tests/e2e -v
