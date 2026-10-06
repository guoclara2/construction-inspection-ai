$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
& (Join-Path $PSScriptRoot 'stop-local.ps1')
$env:PYTHONUTF8 = '1'
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Install Node.js 22 LTS or newer, then reopen this window.' }
if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) { throw 'npm.cmd is missing. Repair the Node.js installation.' }
$backend = Join-Path $root 'backend'
$python = Join-Path $backend '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    & python -m venv (Join-Path $backend '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 or newer is required.' }
}
& $python -m pip install -r (Join-Path $backend 'requirements-dev.txt')
if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed; see the error above.' }
& $python -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Backend dependency versions are inconsistent.' }
Push-Location (Join-Path $root 'admin-web')
try {
    & npm.cmd ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Frontend installation failed. Do not start the app until installation succeeds. If EPERM persists, close the editor terminal running this project.' }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
} finally { Pop-Location }
if (-not (Test-Path (Join-Path $backend '.env'))) {
    Copy-Item -LiteralPath (Join-Path $backend '.env.example') -Destination (Join-Path $backend '.env')
}
Write-Host 'Installation verified. Run scripts/start-all.bat.'
