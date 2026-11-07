param(
  [ValidateSet("start", "status", "stop")]
  [string]$Action = "start",
  [int]$Port = 9242,
  [int]$StartupTimeoutSeconds = 20
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$RuntimeRoot = Join-Path $RepoRoot ".local\provider-console\$Port"
$PidFile = Join-Path $RuntimeRoot "provider-console.pid"
$StdoutLog = Join-Path $RuntimeRoot "provider-console.stdout.log"
$StderrLog = Join-Path $RuntimeRoot "provider-console.stderr.log"
$HealthUrl = "http://127.0.0.1:$Port/healthz"

function Get-ConsoleHealth {
  try {
    $response = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 2
    if (
      $response.ok -eq $true -and
      $response.status -eq "control_ui_ready" -and
      $response.service -eq "model-provider-gateway-provider-console" -and
      $response.api_schema -eq "model_provider_gateway.http.v1"
    ) {
      return $response
    }
  } catch {
    return $null
  }
  return $null
}

function Get-ManagedProcess {
  if (-not (Test-Path -LiteralPath $PidFile)) {
    return $null
  }
  $savedPid = [int](Get-Content -LiteralPath $PidFile -Raw).Trim()
  $process = Get-CimInstance Win32_Process -Filter "ProcessId = $savedPid" -ErrorAction SilentlyContinue
  if ($null -eq $process) {
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    return $null
  }
  $commandLine = [string]$process.CommandLine
  $portPattern = "--port\s+$Port(?:\s|$)"
  if (
    $process.Name -notmatch '^python(?:w)?\.exe$' -or
    $commandLine -notmatch 'model_provider_gateway' -or
    $commandLine -notmatch '\bui\b' -or
    $commandLine -notmatch $portPattern
  ) {
    throw "PID $savedPid does not belong to the managed provider console; refusing to stop it"
  }
  return $process
}

function Write-Status {
  $health = Get-ConsoleHealth
  $managed = Get-ManagedProcess
  [ordered]@{
    status = if ($null -ne $health) { "healthy" } else { "stopped_or_unhealthy" }
    ownership = if ($null -ne $managed) {
      "managed"
    } elseif ($null -ne $health) {
      "external_matching_service"
    } else {
      "none"
    }
    url = "http://127.0.0.1:$Port/"
    health_url = $HealthUrl
    managed_pid = if ($null -ne $managed) { [int]$managed.ProcessId } else { $null }
    provider_call_performed = $false
    proxy_started = $false
    stdout_log = $StdoutLog
    stderr_log = $StderrLog
  } | ConvertTo-Json -Depth 4
}

New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null

if ($Action -eq "status") {
  Write-Status
  exit 0
}

if ($Action -eq "stop") {
  $managed = Get-ManagedProcess
  if ($null -eq $managed) {
    # A matching healthy service without this port's PID file is deliberately
    # treated as external. Never stop a process based on port ownership alone.
    Write-Status
    exit 0
  }
  Stop-Process -Id ([int]$managed.ProcessId) -ErrorAction Stop
  Wait-Process -Id ([int]$managed.ProcessId) -Timeout 10 -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
  Write-Status
  exit 0
}

$existingHealth = Get-ConsoleHealth
if ($null -ne $existingHealth) {
  Write-Status
  exit 0
}

$listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
if ($null -ne $listener) {
  throw "Port $Port is already occupied by an unhealthy or unrelated process"
}

$python = if (Test-Path -LiteralPath (Join-Path $RepoRoot ".venv\Scripts\python.exe")) {
  Join-Path $RepoRoot ".venv\Scripts\python.exe"
} else {
  (Get-Command python -ErrorAction Stop).Source
}

$oldPythonPath = $env:PYTHONPATH
try {
  $env:PYTHONPATH = Join-Path $RepoRoot "src"
  $process = Start-Process -FilePath $python `
    -ArgumentList @("-m", "model_provider_gateway", "ui", "--host", "127.0.0.1", "--port", $Port) `
    -WorkingDirectory $RepoRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $StdoutLog `
    -RedirectStandardError $StderrLog `
    -PassThru
} finally {
  $env:PYTHONPATH = $oldPythonPath
}

Set-Content -LiteralPath $PidFile -Value $process.Id -Encoding ascii
$deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
do {
  Start-Sleep -Milliseconds 250
  if ($process.HasExited) {
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    throw "Provider console exited during startup; inspect $StderrLog"
  }
  $health = Get-ConsoleHealth
} while ($null -eq $health -and (Get-Date) -lt $deadline)

if ($null -eq $health) {
  Stop-Process -Id $process.Id -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
  throw "Provider console did not become healthy within $StartupTimeoutSeconds seconds"
}

Write-Status
