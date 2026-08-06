# Windows：生成样例（如缺）→ API sidecar → Tauri NSIS
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root
$env:CARGO_TARGET_DIR = Join-Path $Root "apps/desktop/src-tauri/target"

$Sample = Join-Path $Root "samples/demo_journal_2022.xlsx"
if (-not (Test-Path $Sample)) {
  Write-Host "→ 生成试用样例"
  uv run python (Join-Path $Root "scripts/generate_demo_journal.py")
}

Write-Host "→ 构建 API sidecar"
& (Join-Path $Root "scripts/build-api-sidecar.ps1")

Write-Host "→ 安装前端依赖（如需）"
Push-Location (Join-Path $Root "apps/desktop")
npm install
Write-Host "→ Tauri build"
npm run tauri build
Pop-Location

Write-Host ""
Write-Host "完成。产物通常在："
Write-Host "  apps/desktop/src-tauri/target/release/bundle/nsis/"
Write-Host "试用样例：samples/demo_journal_2022.xlsx"
