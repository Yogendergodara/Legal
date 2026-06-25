# Start legal_ai_platform on :8080 (Python 3.11-3.13).
# Prereq: document-mcp on :8003; retrieval-mcp on :8001 optional (research only).
param(
    [switch]$Replace,
    [switch]$Status
)

$ErrorActionPreference = "Stop"
$LegalAi = Split-Path -Parent $PSScriptRoot
$LegalRoot = Split-Path -Parent $LegalAi
$PlatformDir = Join-Path $LegalRoot "legal_ai_platform"
$EnvFile = Join-Path $PlatformDir ".env"
$PidFile = Join-Path $PSScriptRoot ".legal_ai_platform.pid"

function Get-Port8080Pids {
    $found = @()
    netstat -ano | Select-String ":8080.*LISTENING" | ForEach-Object {
        $parts = ($_.Line -split '\s+') | Where-Object { $_ }
        if ($parts.Count -ge 1 -and $parts[-1] -match '^\d+$') {
            $found += [int]$parts[-1]
        }
    }
    return ($found | Select-Object -Unique)
}

function Test-PythonVersion {
    $raw = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ($LASTEXITCODE -ne 0) {
        Write-Error "python not found on PATH"
    }
    $parts = $raw.Trim().Split(".")
    $major = [int]$parts[0]
    $minor = [int]$parts[1]
    if ($major -ne 3 -or $minor -lt 11 -or $minor -gt 13) {
        Write-Error "Python 3.11-3.13 required; found $raw"
    }
    Write-Host "Python $raw OK"
}

if (-not (Test-Path $PlatformDir)) {
    Write-Error "Missing $PlatformDir"
}

if (-not (Test-Path $EnvFile)) {
    if (Test-Path (Join-Path $PlatformDir ".env.example")) {
        Copy-Item (Join-Path $PlatformDir ".env.example") $EnvFile
        Write-Host "Created $EnvFile from .env.example"
    } else {
        Write-Error "Missing $EnvFile - copy from .env.example"
    }
}

Get-Content $EnvFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#") -and $line -match "=") {
        $name, $value = $line.Split("=", 2)
        Set-Item -Path "env:$name" -Value $value
    }
}

$env:DOCUMENT_SERVER_URL = if ($env:DOCUMENT_SERVER_URL) { $env:DOCUMENT_SERVER_URL } else { "http://localhost:8003" }
$env:RETRIEVAL_SERVER_URL = if ($env:RETRIEVAL_SERVER_URL) { $env:RETRIEVAL_SERVER_URL } else { "http://localhost:8001" }

if ($Status) {
    $pids = Get-Port8080Pids
    Write-Host "Port 8080 listeners: $(if ($pids) { $pids -join ', ' } else { '(none)' })"
    if (Test-Path $PidFile) {
        Write-Host "Pidfile: $(Get-Content $PidFile -Raw)"
    }
    try {
        $resp = Invoke-RestMethod -Uri "http://localhost:8080/agents" -TimeoutSec 5
        Write-Host "Agents:"
        $resp | ConvertTo-Json -Depth 5
    } catch {
        Write-Host "Agents check failed: $_"
    }
    exit 0
}

$existing = Get-Port8080Pids
if ($existing.Count -gt 0) {
    if (-not $Replace) {
        Write-Error @"
Port 8080 already in use by PID(s): $($existing -join ', ').
Restart with -Replace or stop the existing platform process.
"@
    }
    foreach ($listenerPid in $existing) {
        Stop-Process -Id $listenerPid -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1
}

Test-PythonVersion

Write-Host "Installing editable deps (document_core, review_agent, research, platform)..."
& python -m pip install -q -e (Join-Path $LegalRoot "document_core")
& python -m pip install -q -e (Join-Path $LegalRoot "review\review_agent")
& python -m pip install -q -e (Join-Path $LegalRoot "Legal_Ai_Research_Agent")
& python -m pip install -q -e "$PlatformDir[dev]"

Set-Location $PlatformDir
Write-Host "legal_ai_platform -> http://localhost:8080"
Write-Host "DOCUMENT_SERVER_URL=$env:DOCUMENT_SERVER_URL"

$proc = Start-Process -FilePath "python" -ArgumentList @(
    "-m", "uvicorn", "legal_ai_platform.gateway.app:app",
    "--host", "0.0.0.0", "--port", "8080"
) -PassThru -WindowStyle Hidden
$proc.Id | Set-Content -Path $PidFile -Encoding ascii
Write-Host "Started PID $($proc.Id) (pidfile: $PidFile)"
Start-Sleep -Seconds 2
try {
    Invoke-RestMethod -Uri "http://localhost:8080/agents" -TimeoutSec 10 | Out-Null
    Write-Host "Platform ready."
} catch {
    Write-Host "WARNING: platform not responding yet - check logs / retry -Status"
}
