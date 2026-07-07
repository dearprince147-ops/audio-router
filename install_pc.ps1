# Check for Python
if (-Not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Installing Python..." -ForegroundColor Yellow
    winget install Python.Python.3.11
}

# Download and run the sender
 $url = "https://raw.githubusercontent.com/dearprince147-ops/audio-router/main/windows_sender.py"
Invoke-WebRequest -Uri $url -OutFile "windows_sender.py"
python -m pip install sounddevice rich pycaw
python windows_sender.py
