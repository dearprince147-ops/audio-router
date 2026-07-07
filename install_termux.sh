#!/data/data/com.termux/files/usr/bin/bash
# ──────────────────────────────────────────────────────────────
#  Audio Router - Termux Setup (run this on your Android phone)
#
#  Usage:
#    curl -sL https://raw.githubusercontent.com/dearprince147-ops/audio-router/main/install_termux.sh | bash
#
#  Or clone the repo and run:
#    bash install_termux.sh
# ──────────────────────────────────────────────────────────────
set -e

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
MAGENTA='\033[0;35m'
NC='\033[0m' # No Color

step()  { echo -e "\n${CYAN}>> $1${NC}"; }
ok()    { echo -e "   ${GREEN}$1${NC}"; }
warn()  { echo -e "   ${YELLOW}$1${NC}"; }

echo -e "\n${MAGENTA}=== Audio Router - Termux Setup ===${NC}\n"

# --- 1. Update package repos ---
step "Updating Termux packages..."
pkg update -y && pkg upgrade -y
ok "Packages updated."

# --- 2. Install system dependencies ---
step "Installing system packages (python, ffmpeg, cava, pulseaudio)..."
pkg install -y python ffmpeg cava pulseaudio
ok "System packages installed."

# --- 3. Install Python dependencies ---
step "Installing Python libraries (rich)..."
pip install --upgrade pip 2>/dev/null
pip install rich
ok "Python libraries installed."

# --- 4. Download receiver script ---
step "Downloading termux_receiver.py..."
mkdir -p ~/audio-router
curl -sL https://raw.githubusercontent.com/dearprince147-ops/audio-router/main/termux_receiver.py -o ~/audio-router/termux_receiver.py
chmod +x ~/audio-router/termux_receiver.py
ok "Saved to ~/audio-router/termux_receiver.py"

# --- 5. Create a launcher alias ---
step "Creating 'audiorouter' command alias..."
ALIAS_LINE='alias audiorouter="python ~/audio-router/termux_receiver.py"'
BASHRC="$HOME/.bashrc"

if [ -f "$BASHRC" ] && grep -qF "audiorouter" "$BASHRC"; then
    ok "Alias already exists in .bashrc"
else
    echo "" >> "$BASHRC"
    echo "# Audio Router" >> "$BASHRC"
    echo "$ALIAS_LINE" >> "$BASHRC"
    ok "Added 'audiorouter' alias to .bashrc"
fi

# --- Done! ---
echo -e "\n${GREEN}=== Setup complete! ===${NC}"
echo -e "\n${CYAN}To start receiving audio from your PC:${NC}"
echo -e "  ${MAGENTA}python ~/audio-router/termux_receiver.py${NC}"
echo -e "\n${CYAN}Or simply type:${NC}"
echo -e "  ${MAGENTA}audiorouter${NC}"
echo -e "\n${YELLOW}Make sure windows_sender.py is running on your PC first!${NC}\n"

# Ask if user wants to start now
read -p "Start Audio Router now? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    python ~/audio-router/termux_receiver.py
fi
