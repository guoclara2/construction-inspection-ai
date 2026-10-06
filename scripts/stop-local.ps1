$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$all = @(Get-CimInstance Win32_Process)
$owned = @($all | Where-Object {
    $_.Name -in @('node.exe','python.exe','pythonw.exe') -and
    $_.CommandLine -and $_.CommandLine.Contains($root) -and
    ($_.CommandLine -match 'vite[\\/]bin[\\/]vite.js' -or
        ($_.CommandLine -match '-m uvicorn app.main:app' -and $_.CommandLine -match '--port\s+18090(?:\s|$)'))
})
function Stop-OwnedTree($processId) {
    foreach ($child in @($all | Where-Object ParentProcessId -eq $processId)) { Stop-OwnedTree $child.ProcessId }
    Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
    Wait-Process -Id $processId -Timeout 15 -ErrorAction SilentlyContinue
    $remaining = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($remaining -and -not $remaining.HasExited) {
        throw "Process $processId did not stop; close the inspection service before reinstalling."
    }
}
foreach ($process in $owned) { Stop-OwnedTree $process.ProcessId }
Write-Host 'Local inspection services stopped. Other projects were not selected.'
