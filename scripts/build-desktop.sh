#!/usr/bin/env bash
# macOS：生成样例（如缺）→ API sidecar → Tauri dmg/app
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PATH="${HOME}/.cargo/bin:${PATH}"
# Keep bundle output inside the repo (avoid Cursor/sandbox CARGO_TARGET_DIR).
export CARGO_TARGET_DIR="$ROOT/apps/desktop/src-tauri/target"

if [[ ! -f "$ROOT/samples/demo_journal_2022.xlsx" ]]; then
  echo "→ 生成试用样例"
  uv run python "$ROOT/scripts/generate_demo_journal.py"
fi

echo "→ 构建 API sidecar"
bash "$ROOT/scripts/build-api-sidecar.sh"

echo "→ 安装前端依赖（如需）"
(cd "$ROOT/apps/desktop" && npm install)

echo "→ Tauri build"
(cd "$ROOT/apps/desktop" && npm run tauri build)

echo ""
echo "完成。产物通常在："
echo "  apps/desktop/src-tauri/target/release/bundle/dmg/"
echo "  apps/desktop/src-tauri/target/release/bundle/macos/"
echo "试用样例：samples/demo_journal_2022.xlsx"
