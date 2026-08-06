# Build Windows x64 audit-api sidecar for Tauri externalBin.
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$Dest = Join-Path $Root "apps/desktop/src-tauri/binaries"
New-Item -ItemType Directory -Force -Path $Dest | Out-Null

$Triple = (rustc -vV | Select-String '^host:').ToString().Split(' ')[1]
if (-not $Triple) {
  throw "无法检测 rustc host triple"
}

Write-Host "→ uv sync (含 pyinstaller)"
uv sync --group dev

Write-Host "→ PyInstaller audit-api (host=$Triple)"
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

$Src = Join-Path $Dist "audit-api.exe"
$Out = Join-Path $Dest "audit-api-$Triple.exe"
Copy-Item -Force $Src $Out
Write-Host "→ sidecar: $Out"
Get-Item $Out | Format-List Name, Length, FullName
