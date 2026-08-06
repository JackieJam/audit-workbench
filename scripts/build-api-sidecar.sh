#!/usr/bin/env bash
# 构建 macOS / 当前平台的 audit-api sidecar，供 Tauri externalBin 使用。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PATH="${HOME}/.cargo/bin:${PATH}"

DEST="$ROOT/apps/desktop/src-tauri/binaries"
mkdir -p "$DEST"

if ! rustc -vV >/dev/null 2>&1; then
  if command -v rustup >/dev/null 2>&1; then
    rustup default stable
  else
    echo "需要安装 Rust（rustup）以检测 host triple" >&2
    exit 1
  fi
fi

TRIPLE="$(rustc -vV | awk '/^host:/{print $2}')"
if [[ -z "${TRIPLE}" ]]; then
  echo "无法检测 rustc host triple" >&2
  exit 1
fi

echo "→ uv sync (含 pyinstaller)"
uv sync --group dev

echo "→ PyInstaller audit-api (host=${TRIPLE})"
rm -rf "$ROOT/dist-sidecar" "$ROOT/build/audit_api_sidecar"
uv run pyinstaller \
  --noconfirm \
  --clean \
  --distpath "$ROOT/dist-sidecar" \
  --workpath "$ROOT/build/audit_api_sidecar" \
  "$ROOT/scripts/audit_api_sidecar.spec"

SRC="$ROOT/dist-sidecar/audit-api"
OUT="$DEST/audit-api-${TRIPLE}"
cp "$SRC" "$OUT"
chmod +x "$OUT"
echo "→ sidecar: $OUT"
ls -lh "$OUT"
