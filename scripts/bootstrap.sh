#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "→ 安装 Python 依赖"
uv sync

echo "→ 安装前端依赖"
(cd apps/desktop && npm install)

echo "→ 运行 engine 测试"
uv run pytest

echo ""
echo "开发：开两个终端"
echo "  1) ./scripts/dev-api.sh"
echo "  2) ./scripts/dev-ui.sh"
echo "浏览器打开 http://localhost:${AUDIT_UI_PORT:-5188}"
