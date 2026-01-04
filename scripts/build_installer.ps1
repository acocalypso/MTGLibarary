Param(
  [string]$Name = "MTGLibarary"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

# 1) Build the onedir app bundle (exe + _internal)
& "$repoRoot\scripts\build_exe.ps1" -Name $Name | Out-Host

# 2) Compile the Inno Setup installer
$iss = Join-Path $repoRoot "installer\$Name.iss"
if (-not (Test-Path $iss)) {
  Write-Error "Missing Inno Setup script: $iss"
}

# Try ISCC from PATH, otherwise default install location
$cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
$iscc = $null
if ($null -ne $cmd) {
  $iscc = $cmd.Source
}
if (-not $iscc) {
  $default = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
  if (Test-Path $default) {
    $iscc = $default
  }
}

if (-not $iscc) {
  Write-Error "ISCC.exe not found. Install Inno Setup 6+ (e.g. via winget: winget install InnoSetup.InnoSetup)"
}

& $iscc $iss | Out-Host

Write-Host "Installer built in: $repoRoot\dist\$Name-Setup.exe"