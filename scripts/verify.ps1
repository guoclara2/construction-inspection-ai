$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw '缺少 backend/.venv/Scripts/python.exe' }
$env:PYTHONUTF8 = '1'
& $python (Join-Path $projectRoot 'scripts\verify.py')
if ($LASTEXITCODE -ne 0) { throw '验证失败' }
