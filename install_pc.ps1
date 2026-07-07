# Audio Router - Windows PC Setup
# Usage (run in PowerShell as Administrator):
#   irm https://raw.githubusercontent.com/dearprince147-ops/audio-router/main/install_pc.ps1 | iex

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Step($msg) { Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "   $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "   $msg" -ForegroundColor Yellow }

# --- Elevate to admin if needed (firewall rules require it) ---
if (-Not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Warn "Restarting as Administrator (needed for firewall rules)..."
    Start-Process powershell "-ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
    exit
}

Write-Host "`n=== Audio Router - Windows Setup ===`n" -ForegroundColor Magenta

# --- 1. Python ---
Write-Step "Checking Python..."
if (Get-Command python -ErrorAction SilentlyContinue) {
    $pyVer = python --version 2>&1
    Write-Ok "Found: $pyVer"
} else {
    Write-Warn "Python not found -- installing via winget..."
    winget install Python.Python.3.11 --accept-package-agreements --accept-source-agreements
    # Refresh PATH so python is available in this session
    $env:Path = [System.Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                [System.Environment]::GetEnvironmentVariable('Path', 'User')
    if (-Not (Get-Command python -ErrorAction SilentlyContinue)) {
        Write-Host "ERROR: Python installation failed. Install manually and re-run." -ForegroundColor Red
        exit 1
    }
    Write-Ok "Python installed."
}

# --- 2. pip dependencies ---
Write-Step "Installing Python dependencies..."
python -m pip install --upgrade pip --quiet 2>$null
python -m pip install rich soundcard numpy --quiet
Write-Ok "Dependencies installed (rich, soundcard, numpy)."

# --- 3. Firewall rules ---
Write-Step "Configuring Windows Firewall..."

$rules = @(
    @{ Name='AudioRouter-TCP-5005';  Protocol='TCP'; Port=5005;  Dir='Inbound' },
    @{ Name='AudioRouter-UDP-50000'; Protocol='UDP'; Port=50000; Dir='Inbound' }
)

foreach ($r in $rules) {
    $existing = Get-NetFirewallRule -DisplayName $r.Name -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Ok "Firewall rule '$($r.Name)' already exists."
    } else {
        New-NetFirewallRule -DisplayName $r.Name `
            -Direction Inbound `
            -Protocol $r.Protocol `
            -LocalPort $r.Port `
            -Action Allow `
            -Profile Private,Domain `
            -Description "Audio Router: allows phone to connect" | Out-Null
        Write-Ok "Created firewall rule: $($r.Name)"
    }
}

# --- 4. Download sender script ---
Write-Step "Downloading latest windows_sender.py..."
$url = "https://raw.githubusercontent.com/dearprince147-ops/audio-router/main/windows_sender.py"
Invoke-WebRequest -Uri $url -OutFile "windows_sender.py" -UseBasicParsing
Write-Ok "Saved windows_sender.py"

# --- 5. Launch ---
Write-Host "`n=== Setup complete! Starting Audio Router... ===`n" -ForegroundColor Green
python windows_sender.py
