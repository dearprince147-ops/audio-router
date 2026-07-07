<div align="center">

# 🔊 Audio Router

**Stream your Windows PC's audio to your Android phone over Wi-Fi**

*with real-time [cava](https://github.com/karlstav/cava) visualizer & volume control*

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform: Windows → Android](https://img.shields.io/badge/Platform-Windows_%E2%86%92_Android-brightgreen)](#)
[![Termux](https://img.shields.io/badge/Termux-Compatible-orange)](#)

</div>

---

## How It Works

```
┌──────────────────────┐          Wi-Fi (LAN)          ┌──────────────────────┐
│    Windows PC        │ ──────────────────────────────▶│   Android Phone      │
│                      │    TCP stream (raw s16le)      │   (Termux)           │
│  windows_sender.py   │                                │  termux_receiver.py  │
│  ┌────────────────┐  │   1. Phone broadcasts UDP      │  ┌────────────────┐  │
│  │ WASAPI Loopback│  │      discovery on port 50000   │  │ pacat          │  │
│  │ (soundcard)    │  │   2. PC replies with TCP port   │  │ (audio output) │  │
│  └────────────────┘  │   3. Phone connects via TCP     │  ├────────────────┤  │
│         ▼            │   4. PC sends audio header +    │  │ cava           │  │
│  Captures all system │      raw PCM audio stream       │  │ (visualizer)   │  │
│  audio in real time  │                                │  └────────────────┘  │
└──────────────────────┘                                └──────────────────────┘
```

**In short:** Everything playing on your PC speakers comes out of your phone's speaker instead, with a gorgeous audio visualizer and volume control.

---

## Quick Start

### 🖥️ On Your Windows PC (one-time setup)

Open **PowerShell as Administrator** and run:

```powershell
irm https://raw.githubusercontent.com/dearprince147-ops/audio-router/main/install_pc.ps1 | iex
```

This will:
- ✅ Install Python (if missing)
- ✅ Install dependencies (`soundcard`, `numpy`, `rich`)
- ✅ Open firewall ports (TCP 5005, UDP 50000)
- ✅ Add `start-audio` command to PowerShell
- ✅ Launch the audio sender

**Next time, just open PowerShell and type:**
```powershell
start-audio
```

### 📱 On Your Android Phone (Termux)

Open **[Termux](https://f-droid.org/en/packages/com.termux/)** and run:

```bash
curl -sL https://raw.githubusercontent.com/dearprince147-ops/audio-router/main/install_termux.sh | bash
```

This will:
- ✅ Install all packages (`python`, `cava`, `pulseaudio`)
- ✅ Install Python dependencies (`rich`)
- ✅ Download the receiver script
- ✅ Create an `audiorouter` command for easy launching

**After setup, just run:**
```bash
audiorouter
```

---

## Controls

### Phone (Termux)

| Key | Action |
|-----|--------|
| `+` or `=` | Volume up (5% steps) |
| `-` | Volume down (5% steps) |
| `q` | Quit |

Volume is shown in the terminal title bar.

### PC (PowerShell)

| Key | Action |
|-----|--------|
| `Ctrl+C` | Stop the sender |

---

## Prerequisites

| Component | PC (Windows 10/11) | Phone (Android) |
|-----------|-------------------|------------------|
| **OS** | Windows 10 or 11 | Android 7+ |
| **App** | PowerShell | [Termux](https://f-droid.org/en/packages/com.termux/) (from F-Droid) |
| **Network** | Same Wi-Fi network | Same Wi-Fi network |
| **Python** | 3.8+ (auto-installed) | Auto-installed |

> ⚠️ **Important:** Install Termux from [F-Droid](https://f-droid.org/en/packages/com.termux/), **not** the Play Store. The Play Store version is outdated and broken.

---

## Manual Installation

If you prefer to set things up manually:

### Windows PC

```powershell
# Install dependencies
pip install rich soundcard numpy

# Open firewall (run as Administrator)
New-NetFirewallRule -DisplayName 'AudioRouter-TCP-5005' -Direction Inbound -Protocol TCP -LocalPort 5005 -Action Allow
New-NetFirewallRule -DisplayName 'AudioRouter-UDP-50000' -Direction Inbound -Protocol UDP -LocalPort 50000 -Action Allow

# Run
python windows_sender.py
```

### Termux (Android)

```bash
# Install packages
pkg install python cava pulseaudio
pip install rich

# Run
python termux_receiver.py
```

---

## Usage

1. **Start the sender** on your PC — it will show "waiting for phone"
2. **Start the receiver** on your phone — it will auto-discover the PC
3. **Play any audio** on your PC — it streams to your phone
4. **cava** launches automatically with a colorful visualizer
5. Use **+/-** to control volume, **q** to quit

If auto-discovery doesn't find your PC, the receiver will ask you to enter your PC's IP address manually.

> 💡 **Note:** Audio still plays on your PC too — WASAPI loopback captures a *copy* of the audio. Both devices will output sound.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| **"No PCs found"** | Make sure both devices are on the same Wi-Fi. Check that the Windows firewall rules were created (see Manual Installation). |
| **Phone connects but no sound** | Run `pulseaudio --start --load=module-sles-sink` manually in Termux. Check `~/.cache/audiorouter/audio.log` for errors. |
| **cava shows nothing** | The audio is still playing — cava just didn't connect to the FIFO. Restart the receiver. |
| **Choppy / stuttery audio** | Move closer to your Wi-Fi router. Your network may have high latency or packet loss. |
| **"Connection refused"** | The sender isn't running, or the firewall is blocking port 5005. |
| **Termux install fails with mirror error** | Run `termux-change-repo` and select a different mirror, then retry. |
| **`start-audio` not recognized** | Close and reopen PowerShell. The command is loaded from your profile on startup. |

---

## How It Works (Technical)

1. **Discovery**: The phone sends a UDP broadcast (`AUDIOROUTER_DISCOVER`) on port 50000. The PC responds with its hostname and TCP port.
2. **Connection**: The phone connects to the PC via TCP on port 5005.
3. **Header**: The PC sends a JSON header line with the audio format (`{"samplerate": 48000, "channels": 2, "format": "s16le"}`).
4. **Streaming**: The PC continuously captures system audio via WASAPI loopback, converts it to 16-bit signed PCM, and streams it over TCP.
5. **Playback**: The phone pipes the raw PCM to `pacat` (PulseAudio native player) for speaker output and to a FIFO that `cava` reads for visualization.
6. **Volume**: The receiver uses `pactl` to control the PulseAudio sink volume in 5% steps.

---

## Project Structure

```
audio-router/
├── windows_sender.py      # PC-side: captures & streams system audio
├── termux_receiver.py     # Phone-side: receives audio, plays & visualizes
├── install_pc.ps1         # One-command Windows setup (adds start-audio command)
├── install_termux.sh      # One-command Termux setup (adds audiorouter command)
└── README.md
```

---

## License

MIT — do whatever you want with it.

---

<div align="center">

Made with ❤️ for wireless audio freedom

</div>
