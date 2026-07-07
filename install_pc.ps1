# Audio Router - Windows PC bootstrap
# Usage: irm https://raw.githubusercontent.com/dearprince147-ops/audio-router/main/install_pc.ps1 | iex

if (-Not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Installing Python..." -ForegroundColor Yellow
    winget install Python.Python.3.11
}

$url = "https://raw.githubusercontent.com/dearprince147-ops/audio-router/main/windows_sender.py"
Invoke-WebRequest -Uri $url -OutFile "windows_sender.py"

python -m pip install --upgrade pip
python -m pip install rich soundcard numpy

python windows_sender.py
