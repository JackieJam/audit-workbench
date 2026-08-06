# Build onedir audit-api into Tauri resources (Windows).
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$DestRes = Join-Path $Root "apps/desktop/src-tauri/resources/audit-api"
New-Item -ItemType Directory -Force -Path (Join-Path $Root "apps/desktop/src-tauri/binaries") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Root "apps/desktop/src-tauri/resources") | Out-Null

Write-Host "→ uv sync (含 pyinstaller)"
uv sync --group dev

Write-Host "→ PyInstaller audit-api (onedir)"
$Dist = Join-Path $Root "dist-sidecar"
$Work = Join-Path $Root "build/audit_api_sidecar"
if (Test-Path $Dist) { Remove-Item -Recurse -Force $Dist }
if (Test-Path $Work) { Remove-Item -Recurse -Force $Work }

uv run pyinstaller `
  --noconfirm `
  --clean `
  --distpath $Dist `
  --workpath $Work `
  (Join-Path $Root "scripts/audit_api_sidecar.spec")

$SrcDir = Join-Path $Dist "audit-api"
if (-not (Test-Path $SrcDir)) { throw "PyInstaller 未产出目录: $SrcDir" }

if (Test-Path $DestRes) { Remove-Item -Recurse -Force $DestRes }
Copy-Item -Recurse $SrcDir $DestRes

Write-Host "→ resources sidecar: $DestRes"
Get-ChildItem $DestRes | Select-Object -First 10 Name
