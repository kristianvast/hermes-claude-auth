#Requires -Version 5.1
<#
.SYNOPSIS
    Install Claude Code OAuth bypass for hermes-agent on Windows.
.DESCRIPTION
    Copies anthropic_billing_bypass.py to %LOCALAPPDATA%\hermes\patches\ and
    installs the import hook into the hermes-agent venv's site-packages.
    Hermes installation directory: %LOCALAPPDATA%\hermes\hermes-agent\
#>
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Ok   { param($msg) Write-Host "[OK] $msg" -ForegroundColor Green }
function Write-Fail { param($msg) Write-Host "[X]  $msg" -ForegroundColor Red }
function Write-Warn { param($msg) Write-Host "[!]  $msg" -ForegroundColor Yellow }

$ScriptDir      = Split-Path -Parent $MyInvocation.MyCommand.Path
$HermesAgentDir = Join-Path $env:LOCALAPPDATA 'hermes\hermes-agent'
$PatchesDir     = Join-Path $env:LOCALAPPDATA 'hermes\patches'
$Marker         = '# hermes-claude-auth managed'

# --- Check hermes-agent exists ---
if (-not (Test-Path $HermesAgentDir -PathType Container)) {
    Write-Fail "hermes-agent not found at $HermesAgentDir"
    Write-Host "    Install hermes-agent first: https://github.com/nousresearch/hermes-agent"
    exit 1
}

# --- Locate venv ---
if ($env:HERMES_VENV -and (Test-Path $env:HERMES_VENV -PathType Container)) {
    $VenvDir = $env:HERMES_VENV
} elseif (Test-Path (Join-Path $HermesAgentDir 'venv') -PathType Container) {
    $VenvDir = Join-Path $HermesAgentDir 'venv'
} elseif (Test-Path (Join-Path $HermesAgentDir '.venv') -PathType Container) {
    $VenvDir = Join-Path $HermesAgentDir '.venv'
} else {
    Write-Fail "No virtualenv found in $HermesAgentDir (checked venv\, .venv\, and `$env:HERMES_VENV)"
    exit 1
}

# --- Locate Python (Windows venv uses Scripts\python.exe) ---
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'
if (-not (Test-Path $VenvPython)) {
    Write-Fail "Python not found at $VenvPython"
    exit 1
}

# --- Get site-packages path ---
$SitePackages = & $VenvPython -c "import site; print(site.getsitepackages()[0] if site.getsitepackages() else site.getusersitepackages())" 2>$null
if (-not $SitePackages -or -not (Test-Path $SitePackages -PathType Container)) {
    Write-Fail "site-packages directory does not exist: $SitePackages"
    exit 1
}

# --- Copy patch file ---
if (-not (Test-Path $PatchesDir -PathType Container)) {
    New-Item -ItemType Directory -Path $PatchesDir -Force | Out-Null
}
Copy-Item (Join-Path $ScriptDir 'anthropic_billing_bypass.py') (Join-Path $PatchesDir 'anthropic_billing_bypass.py') -Force
Write-Ok "Copied patch to $PatchesDir\"

# --- Install sitecustomize hook ---
$Sitecustomize = Join-Path $SitePackages 'sitecustomize.py'
$HookSource    = Join-Path $ScriptDir 'sitecustomize_hook.py'

if (-not (Test-Path $Sitecustomize)) {
    Copy-Item $HookSource $Sitecustomize -Force
} elseif ((Get-Content $Sitecustomize -Raw -ErrorAction SilentlyContinue) -match [regex]::Escape($Marker)) {
    Copy-Item $HookSource $Sitecustomize -Force
} else {
    $Backup = "$Sitecustomize.pre-hermes-claude-auth"
    Copy-Item $Sitecustomize $Backup -Force
    Write-Warn "Backed up existing sitecustomize.py to $Backup"
    Copy-Item $HookSource $Sitecustomize -Force
}
Write-Ok "Installed hook into $Sitecustomize"

# --- Check Claude Code credentials ---
$CredFile = Join-Path $env:USERPROFILE '.claude\.credentials.json'
if (Test-Path $CredFile) {
    Write-Ok "Claude Code credentials found at $CredFile"
} else {
    Write-Warn "Claude Code credentials not found at $CredFile"
    Write-Host "    Run: claude auth login --claudeai"
}

Write-Host ""
Write-Ok "Installation complete."
Write-Host "  Patch:  $PatchesDir\anthropic_billing_bypass.py"
Write-Host "  Hook:   $Sitecustomize"
Write-Host "  Venv:   $VenvDir"
Write-Host ""
Write-Host "Restart hermes-gateway to apply the patch." -ForegroundColor Yellow
