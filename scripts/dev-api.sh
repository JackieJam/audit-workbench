#!/usr/bin/env bash
# 启动 FastAPI（开发用）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export AUDIT_API_PORT="${AUDIT_API_PORT:-29180}"
# LLM 直连厂商 API，不走本机 SOCKS/HTTP 代理
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
export NO_PROXY="*"
exec uv run --package audit-api uvicorn audit_api.main:app \
  --host 127.0.0.1 \
  --port "$AUDIT_API_PORT" \
  --reload \
  --reload-dir "$ROOT/apps/api" \
  --reload-dir "$ROOT/packages/engine"
