# Start Legal Review Dev UI (http://localhost:8090)
param(
    [switch]$Replace
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Get-PortListenerPids {
    param([int]$Port)
    $pids = @()
    netstat -ano | Select-String ":$Port\s+.*LISTENING" | ForEach-Object {
        $parts = ($_.Line -split '\s+') | Where-Object { $_ }
        if ($parts.Count -ge 1 -and $parts[-1] -match '^\d+$') {
            $pids += [int]$parts[-1]
        }
    }
    return ($pids | Select-Object -Unique)
}

if (-not (Test-Path ".env")) {
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Write-Host "Created .env from .env.example - set LLM_API_KEY before review."
    }
}

$env:PYTHONPATH = @(
    (Resolve-Path "..\document_core").Path,
    (Resolve-Path "..\review\review_agent").Path,
    (Resolve-Path "..\Legal ai").Path,
    $env:PYTHONPATH
) -join ";"

$depsOk = $false
try {
    & python -c "import fastapi, uvicorn, httpx"
    if ($LASTEXITCODE -eq 0) { $depsOk = $true }
} catch {
    $depsOk = $false
}
if (-not $depsOk) {
    Write-Host "Installing dev UI deps (fastapi, uvicorn, httpx)..."
    & python -m pip install -q fastapi uvicorn httpx python-multipart
}

$port = if ($env:DEV_UI_PORT) { [int]$env:DEV_UI_PORT } else { 8090 }
$listeners = Get-PortListenerPids -Port $port
if ($listeners.Count -gt 0) {
    if ($Replace) {
        Write-Host "Stopping existing listener(s) on port ${port}: $($listeners -join ', ')"
        foreach ($listenerPid in $listeners) {
            Stop-Process -Id $listenerPid -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 1
    } else {
        Write-Host "Port $port already in use (PID $($listeners -join ', '))."
        Write-Host "Dev UI may already be running -> http://localhost:$port"
        Write-Host "To restart: .\run_dev_ui.ps1 -Replace"
        exit 0
    }
}

Write-Host "Dev UI -> http://localhost:$port"
Write-Host "Requires document-mcp on port 8003 (and LLM_API_KEY for review)."
try {
    $null = Invoke-WebRequest -Uri "http://localhost:8080/agents" -TimeoutSec 2 -UseBasicParsing
} catch {
    Write-Host "Platform :8080 not running - use Run review (direct), or: Legal ai\scripts\start_legal_ai_platform.ps1 -Replace"
}
$env:DEV_UI_PORT = "$port"
& python dev_ui_server.py
