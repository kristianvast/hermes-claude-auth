#Requires -Version 5.1
<#
.SYNOPSIS
    Uninstall Claude Code OAuth bypass for hermes-agent on Windows.
.PARAMETER Purge
    Also remove the patch file from %LOCALAPPDATA%\hermes\patches\.
#>
[CmdletBinding()]
param(
    [switch]$Purge
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Ok   { param($msg) Write-Host "[OK] $msg" -ForegroundColor Green }
function Write-Warn { param($msg) Write-Host "[-]  $msg" -ForegroundColor Yellow }

$HermesAgentDir = Join-Path $env:LOCALAPPDATA 'hermes\hermes-agent'
$Marker         = '# hermes-claude-auth managed'

# --- Locate venv ---
$VenvDir = ''
if ($env:HERMES_VENV -and (Test-Path $env:HERMES_VENV -PathType Container)) {
    $VenvDir = $env:HERMES_VENV
} elseif (Test-Path (Join-Path $HermesAgentDir 'venv') -PathType Container) {
    $VenvDir = Join-Path $HermesAgentDir 'venv'
} elseif (Test-Path (Join-Path $HermesAgentDir '.venv') -PathType Container) {
    $VenvDir = Join-Path $HermesAgentDir '.venv'
}

$removedHook  = $false
$restoredHook = $false
$removedPatch = $false

if (-not $VenvDir) {
    Write-Warn "No hermes venv found, skipping hook removal"
} else {
    $VenvPython   = Join-Path $VenvDir 'Scripts\python.exe'
    $SitePackages = ''
    if (Test-Path $VenvPython) {
        $SitePackages = & $VenvPython -c "import site; print(site.getsitepackages()[0])" 2>$null
    }
    if (-not $SitePackages) {
        Write-Warn "Could not detect site-packages, skipping hook removal"
    } else {
        $Sitecustomize = Join-Path $SitePackages 'sitecustomize.py'
        $BackupFile    = "$Sitecustomize.pre-hermes-claude-auth"
        if (-not (Test-Path $Sitecustomize)) {
            Write-Warn "sitecustomize.py not found (already removed)"
        } elseif ((Get-Content $Sitecustomize -Raw -ErrorAction SilentlyContinue) -match [regex]::Escape($Marker)) {
            if (Test-Path $BackupFile) {
                Move-Item $BackupFile $Sitecustomize -Force
                Write-Ok "Restored original sitecustomize.py from backup"
                $restoredHook = $true
            } else {
                Remove-Item $Sitecustomize -Force
                Write-Ok "Removed hook from $SitePackages\sitecustomize.py"
                $removedHook = $true
            }
        } else {
            Write-Warn "sitecustomize.py not ours"
        }
    }
}

if ($Purge) {
    $PatchDir  = Join-Path $env:LOCALAPPDATA 'hermes\patches'
    $PatchFile = Join-Path $PatchDir 'anthropic_billing_bypass.py'
    if (Test-Path $PatchFile) {
        Remove-Item $PatchFile -Force
        Write-Ok "Removed patch from $PatchDir\"
        $removedPatch = $true
    }
    if (Test-Path $PatchDir -PathType Container) {
        $items = Get-ChildItem $PatchDir -Force -ErrorAction SilentlyContinue
        if (-not $items) {
            Remove-Item $PatchDir -Force -ErrorAction SilentlyContinue
        }
    }
}

Write-Host ""
Write-Host "Summary:" -ForegroundColor Green
if ($restoredHook) {
    Write-Host "  - Restored sitecustomize.py from backup"
} elseif ($removedHook) {
    Write-Host "  - Removed sitecustomize.py hook"
} else {
    Write-Host "  - No hook changes needed"
}
if ($removedPatch) {
    Write-Host "  - Removed patch file"
}
