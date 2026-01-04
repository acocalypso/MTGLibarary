Param(
  [string]$Name = "MTGLibarary",
  [switch]$OneFile
)

$ErrorActionPreference = "Stop"

# Run from repo root.
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (-not (Test-Path ".venv")) {
  Write-Error "No .venv folder found. Create one first (or activate your env)."
}

$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
  Write-Error "Expected python at $python"
}

& $python -m pip install --upgrade pip | Out-Host
& $python -m pip install -e ".[dev]" | Out-Host

# Build a Windows GUI app (no console).
if ($OneFile) {
  $onefileArg = "--onefile"
} else {
  $onefileArg = ""
}

& $python -m PyInstaller `
  --noconfirm `
  --clean `
  --name $Name `
  --windowed `
  $onefileArg `
  --paths "src" `
  "src\mtg_cards\__main__.py" | Out-Host

if ($OneFile) {
  Write-Host "Built: $repoRoot\dist\$Name.exe"
} else {
  Write-Host "Built: $repoRoot\dist\$Name\$Name.exe"
  Write-Host "Note: Keep the '_internal' folder next to the exe."
}