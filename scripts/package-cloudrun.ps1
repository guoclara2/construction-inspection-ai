# 微信云托管代码包打包脚本
# 产物：deploy/cloudrun-api.zip（Dockerfile 位于包根目录，控制台「上传代码包」直接使用）
# 内容：后端 API + 数据库迁移 + admin-web 构建产物（单服务形态，容器内同源托管）
# 用法：powershell -File scripts/package-cloudrun.ps1 [-SkipWebBuild]（已构建过 admin-web 可跳过）
param([switch]$SkipWebBuild)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$adminWeb = Join-Path $root 'admin-web'
$deployDir = Join-Path $root 'deploy'
$stageDir = Join-Path $deployDir 'cloudrun-pkg'
$zipPath = Join-Path $deployDir 'cloudrun-api.zip'

if (-not (Test-Path (Join-Path $backend 'docker\Dockerfile.cloudrun'))) {
    throw "缺少 backend/docker/Dockerfile.cloudrun"
}

# 1. 构建 admin-web（保证 dist 与源码一致）
if (-not $SkipWebBuild) {
    if (-not (Test-Path (Join-Path $adminWeb 'package.json'))) { throw "缺少 admin-web" }
    Write-Host "[1/4] 构建 admin-web ..." -ForegroundColor Cyan
    Push-Location $adminWeb
    try { npm run build | Out-Null } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw "admin-web 构建失败" }
} else {
    Write-Host "[1/4] 跳过 admin-web 构建（-SkipWebBuild）" -ForegroundColor Yellow
    if (-not (Test-Path (Join-Path $adminWeb 'dist\index.html'))) { throw "admin-web/dist 不存在：请先 npm run build 或去掉 -SkipWebBuild" }
}

# 2. 清理并搭建暂存目录（即 zip 根目录 = Docker 构建上下文）
Write-Host "[2/4] 暂存代码 ..." -ForegroundColor Cyan
if (Test-Path $stageDir) { Remove-Item $stageDir -Recurse -Force }
New-Item -ItemType Directory -Path $stageDir | Out-Null

# 后端：app（排除 tests/__pycache__）、migrations、scripts、alembic.ini、requirements.txt
$null = robocopy (Join-Path $backend 'app') (Join-Path $stageDir 'app') /E /XD __pycache__ .pytest_cache tests /XF *.pyc
if ($LASTEXITCODE -ge 8) { throw "复制 app 失败（robocode=$LASTEXITCODE）" }
$null = robocopy (Join-Path $backend 'migrations') (Join-Path $stageDir 'migrations') /E /XD __pycache__
if ($LASTEXITCODE -ge 8) { throw "复制 migrations 失败" }
$null = robocopy (Join-Path $backend 'scripts') (Join-Path $stageDir 'scripts') /E /XD __pycache__
if ($LASTEXITCODE -ge 8) { throw "复制 scripts 失败" }
Copy-Item (Join-Path $backend 'alembic.ini') $stageDir
Copy-Item (Join-Path $backend 'requirements.txt') $stageDir
Copy-Item (Join-Path $backend 'constraints.txt') $stageDir

# web 静态产物 → web/dist（Dockerfile.cloudrun 中 COPY web ./web，WEB_DIST_DIR=web/dist）
$null = robocopy (Join-Path $adminWeb 'dist') (Join-Path $stageDir 'web\dist') /E
if ($LASTEXITCODE -ge 8) { throw "复制 admin-web/dist 失败" }

# 3. Dockerfile 与 entrypoint：统一改写为 LF 且 UTF-8 无 BOM（容器内 /bin/sh 要求）
Write-Host "[3/4] 写入 Dockerfile / entrypoint（LF）..." -ForegroundColor Cyan
function Write-LfFile($src, $dest) {
    $content = [System.IO.File]::ReadAllText($src) -replace "`r`n", "`n"
    [System.IO.File]::WriteAllText($dest, $content, (New-Object System.Text.UTF8Encoding($false)))
}
New-Item -ItemType Directory -Path (Join-Path $stageDir 'docker') | Out-Null
Write-LfFile (Join-Path $backend 'docker\Dockerfile.cloudrun') (Join-Path $stageDir 'Dockerfile')
Write-LfFile (Join-Path $backend 'docker\entrypoint.sh') (Join-Path $stageDir 'docker\entrypoint.sh')

# 4. 压缩（用系统自带 bsdtar 生成 zip：路径分隔符为 /，Linux 构建可正常解压；
#    Compress-Archive 会写入 \ 分隔符导致云托管构建失败，不可用）
Write-Host "[4/4] 压缩 deploy/cloudrun-api.zip ..." -ForegroundColor Cyan
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
$tar = Join-Path $env:SystemRoot 'System32\tar.exe'
if (-not (Test-Path $tar)) { throw "系统缺少 tar.exe（Windows 10 1803+ 自带）" }
& $tar -a -cf $zipPath -C $stageDir .
if ($LASTEXITCODE -ne 0) { throw "tar 压缩失败" }

$size = [math]::Round((Get-Item $zipPath).Length / 1MB, 1)
Write-Host ""
Write-Host "打包完成：deploy/cloudrun-api.zip（约 $size MB）" -ForegroundColor Green
Write-Host "暂存目录（可人工检查包内容）：$stageDir"
