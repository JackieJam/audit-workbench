#!/usr/bin/env bash
# 启动 React 前端（Vite 代理 /api → FastAPI）
set -euo pipefail
export AUDIT_API_PORT="${AUDIT_API_PORT:-29180}"
export AUDIT_UI_PORT="${AUDIT_UI_PORT:-5188}"
cd "$(dirname "$0")/../apps/desktop"
npm run dev
