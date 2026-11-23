param(
  [string]$OutputDir = "dist"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$TempRoot = Join-Path $RepoRoot ".local\build-tmp"
New-Item -ItemType Directory -Force -Path $TempRoot | Out-Null

$previousTemp = $env:TEMP
$previousTmp = $env:TMP
try {
  $env:TEMP = $TempRoot
  $env:TMP = $TempRoot
  & python -m build --no-isolation --outdir $OutputDir
  if ($LASTEXITCODE -ne 0) {
    throw "python build failed with exit code $LASTEXITCODE"
  }
} finally {
  $env:TEMP = $previousTemp
  $env:TMP = $previousTmp
}
