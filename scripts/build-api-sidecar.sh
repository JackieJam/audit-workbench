#!/usr/bin/env bash
# 构建 onedir audit-api，拷入 Tauri resources（比 onefile 在 macOS 上更稳）。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export PATH="${HOME}/.cargo/bin:${PATH}"

DEST_RES="$ROOT/apps/desktop/src-tauri/resources/audit-api"
# Keep binaries/ placeholder for docs; onedir lives in resources/
mkdir -p "$ROOT/apps/desktop/src-tauri/binaries"
mkdir -p "$ROOT/apps/desktop/src-tauri/resources"

echo "→ uv sync (含 pyinstaller)"
uv sync --group dev

echo "→ PyInstaller audit-api (onedir)"
rm -rf "$ROOT/dist-sidecar" "$ROOT/build/audit_api_sidecar"
uv run pyinstaller \
  --noconfirm \
  --clean \
  --distpath "$ROOT/dist-sidecar" \
  --workpath "$ROOT/build/audit_api_sidecar" \
  "$ROOT/scripts/audit_api_sidecar.spec"

SRC_DIR="$ROOT/dist-sidecar/audit-api"
if [[ ! -d "$SRC_DIR" ]]; then
  echo "PyInstaller 未产出目录: $SRC_DIR" >&2
  exit 1
fi

rm -rf "$DEST_RES"
mkdir -p "$(dirname "$DEST_RES")"
# Dereference symlinks for a self-contained runtime tree.
rsync -a --copy-links "$SRC_DIR/" "$DEST_RES/"
chmod +x "$DEST_RES/audit-api" 2>/dev/null || chmod +x "$DEST_RES/audit-api.exe"

# Ad-hoc sign so macOS first-launch verification doesn't hang the bootloader/imports.
if [[ "$(uname -s)" == "Darwin" ]]; then
  echo "→ codesign (ad-hoc)"
  find "$DEST_RES" -type f \( -name '*.so' -o -name '*.dylib' -o -name 'audit-api' -o -name '*.bin' \) -print0 \
    | xargs -0 -n 1 codesign --force --sign - --timestamp=none 2>/dev/null || true
  codesign --force --sign - --timestamp=none "$DEST_RES/audit-api" 2>/dev/null || true
fi

# Bundle as a single tarball — Tauri resource walker chokes on nested dylib trees.
RES_ROOT="$ROOT/apps/desktop/src-tauri/resources"
ARCHIVE="$RES_ROOT/audit-api.tar.gz"
mkdir -p "$RES_ROOT/samples"
cp -f "$ROOT/samples/demo_journal_2022.xlsx" "$RES_ROOT/samples/demo_journal_2022.xlsx"
echo "→ pack $ARCHIVE"
tar -C "$RES_ROOT" -czf "$ARCHIVE" audit-api

# Smoke against the extracted tree (not the archive)
echo "→ smoke health"
lsof -ti:29180 2>/dev/null | xargs kill -9 2>/dev/null || true
sleep 0.5
"$DEST_RES/audit-api" >/tmp/audit-api-smoke.log 2>&1 &
SPID=$!
READY=0
for _ in $(seq 1 90); do
  if curl -sf http://127.0.0.1:29180/health >/dev/null; then
    READY=1
    break
  fi
  if ! kill -0 "$SPID" 2>/dev/null; then
    break
  fi
  sleep 0.5
done
kill "$SPID" 2>/dev/null || true
wait "$SPID" 2>/dev/null || true
if [[ "$READY" != "1" ]]; then
  echo "sidecar smoke failed; log:" >&2
  cat /tmp/audit-api-smoke.log >&2 || true
  exit 1
fi

echo "→ resources archive: $ARCHIVE"
ls -lh "$ARCHIVE"
du -sh "$DEST_RES"
