#!/usr/bin/env python3
"""
Audio Router - Termux Receiver
Discovers PCs on the local network, connects to the one you pick, plays
the incoming audio through the phone's speakers, and drives cava full-screen.

Controls (while cava is running):
    +/=   Volume up (5% steps)
    -     Volume down (5% steps)
    q     Quit
"""

import json
import os
import select
import socket
import subprocess
import sys
import termios
import threading
import time
import tty

from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

console = Console()

DISCOVERY_PORT = 50000
DISCOVERY_MSG = b"AUDIOROUTER_DISCOVER"
FIFO_PATH = os.path.expanduser("~/.cache/audiorouter/cava.fifo")
CAVA_CONFIG_PATH = os.path.expanduser("~/.cache/audiorouter/cava_audiorouter.conf")
AUDIO_LOG_PATH = os.path.expanduser("~/.cache/audiorouter/audio.log")


# ── Volume helpers ──────────────────────────────────────────────────────────

def set_volume(pct):
    """Set PulseAudio volume (0-100%)."""
    # Android/Termux PulseAudio usually uses index 0 or 'sles_sink'
    subprocess.run(["pactl", "set-sink-volume", "0", f"{pct}%"], capture_output=True)
    subprocess.run(["pactl", "set-sink-volume", "sles_sink", f"{pct}%"], capture_output=True)


def update_title(pc_name, volume):
    """Update the terminal title bar to show volume (survives cava redraws)."""
    sys.stdout.write(f"\033]0;Audio Router | {pc_name} | Vol: {volume}%\007")
    sys.stdout.flush()


# ── cava config ─────────────────────────────────────────────────────────────

def write_cava_config(samplerate, channels):
    """Write a dedicated cava config that reads from our FIFO.
    We use our own config file so we never touch the user's personal config."""
    os.makedirs(os.path.dirname(CAVA_CONFIG_PATH), exist_ok=True)
    bits = 16
    with open(CAVA_CONFIG_PATH, "w") as f:
        f.write(
            f"[input]\n"
            f"method = fifo\n"
            f"source = {FIFO_PATH}\n"
            f"sample_rate = {samplerate}\n"
            f"sample_bits = {bits}\n"
            f"channels = {'stereo' if channels >= 2 else 'mono'}\n"
            f"\n"
            f"[output]\n"
            f"channels = stereo\n"
            f"\n"
            f"[color]\n"
            f"gradient = 1\n"
            f"gradient_count = 6\n"
            f"gradient_color_1 = '#00ffff'\n"
            f"gradient_color_2 = '#0066ff'\n"
            f"gradient_color_3 = '#ff00ff'\n"
            f"gradient_color_4 = '#ff0066'\n"
            f"gradient_color_5 = '#ffff00'\n"
            f"gradient_color_6 = '#00ff00'\n"
            f"\n"
            f"[smoothing]\n"
            f"noise_reduction = 77\n"
        )


# ── PulseAudio ──────────────────────────────────────────────────────────────

def ensure_pulseaudio():
    """Native Termux has no direct line to the audio hardware -- PulseAudio
    bridges to Android's OpenSL ES / AAudio sink so ffplay has somewhere to
    actually send sound. Without this, ffplay opens, finds no device, and
    exits almost immediately."""
    check = subprocess.run(["pulseaudio", "--check"], capture_output=True)
    if check.returncode == 0:
        return True
    console.print("[cyan]Starting PulseAudio (needed for phone audio output)...[/cyan]")
    subprocess.Popen(
        ["pulseaudio", "--start", "--exit-idle-time=-1", "--load=module-sles-sink"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    for _ in range(20):
        time.sleep(0.3)
        if subprocess.run(["pulseaudio", "--check"], capture_output=True).returncode == 0:
            return True
    console.print("[yellow]Could not confirm PulseAudio started -- audio may not play. "
                  "Try manually: pulseaudio --start --load=module-sles-sink[/yellow]")
    return False


# ── Network discovery ───────────────────────────────────────────────────────

def scan_for_pcs(timeout=4.0, retries=3):
    """Send multiple UDP broadcast packets to discover PCs on the network.
    Wi-Fi routers often drop the first broadcast, so we retry."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(0.5)

    found = {}
    end_time = time.time() + timeout

    for attempt in range(retries):
        try:
            sock.sendto(DISCOVERY_MSG, ("255.255.255.255", DISCOVERY_PORT))
        except OSError:
            pass
        # Also try the <broadcast> alias (common for Android WiFi)
        try:
            sock.sendto(DISCOVERY_MSG, ("<broadcast>", DISCOVERY_PORT))
        except OSError:
            pass

        # Collect replies until we hit the next retry interval or timeout
        wait_until = min(time.time() + (timeout / retries), end_time)
        while time.time() < wait_until:
            try:
                data, addr = sock.recvfrom(1024)
            except socket.timeout:
                continue
            try:
                info = json.loads(data.decode())
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            found[addr[0]] = {
                "ip": addr[0],
                "name": info.get("name", addr[0]),
                "tcp_port": info.get("tcp_port", 5005),
            }

    sock.close()
    return list(found.values())


def choose_pc(pcs):
    table = Table(title="PCs found on your network")
    table.add_column("#", style="cyan")
    table.add_column("Name", style="green")
    table.add_column("IP", style="magenta")
    for i, pc in enumerate(pcs):
        table.add_row(str(i), pc["name"], pc["ip"])
    console.print(table)
    choice = Prompt.ask("Select a PC", choices=[str(i) for i in range(len(pcs))], default="0")
    return pcs[int(choice)]


# ── TCP header ──────────────────────────────────────────────────────────────

def read_header(sock):
    """Read the JSON header line from the TCP stream using a buffer
    instead of one-byte-at-a-time reads."""
    buf = b""
    sock.settimeout(10.0)
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            return None
        buf += chunk
        if b"\n" in buf:
            header_line, remainder = buf.split(b"\n", 1)
            return json.loads(header_line.decode()), remainder
    return None


# ── Audio data pump ─────────────────────────────────────────────────────────

def network_reader(sock, initial_data, player, fifo_ready, stop_event):
    """Reads audio bytes from the PC and fans them out to pacat (speakers)
    and to the cava FIFO (visualizer) at the same time."""
    fifo_fd = None
    for attempt in range(50):
        if stop_event.is_set():
            return
        try:
            fifo_fd = os.open(FIFO_PATH, os.O_WRONLY | os.O_NONBLOCK)
            break
        except OSError:
            time.sleep(0.1)

    fifo_ready.set()
    if fifo_fd is None:
        console.print("[yellow]cava did not attach to the FIFO in time; "
                       "visualizer may stay blank[/yellow]")

    try:
        player_fd = player.stdin.fileno()
        # Write any leftover data from the header read
        if initial_data:
            try:
                os.write(player_fd, initial_data)
            except OSError:
                return
            if fifo_fd is not None:
                try:
                    os.write(fifo_fd, initial_data)
                except OSError:
                    pass

        try:
            sock.settimeout(5.0)
        except OSError:
            return
        while not stop_event.is_set():
            try:
                data = sock.recv(32768)  # large buffer for smooth audio
            except socket.timeout:
                continue
            except OSError:
                break
            if not data:
                break
            try:
                os.write(player_fd, data)
            except OSError:
                break
            if fifo_fd is not None:
                try:
                    os.write(fifo_fd, data)
                except OSError:
                    pass  # cava closed or pipe full -- non-fatal
    finally:
        if fifo_fd is not None:
            os.close(fifo_fd)


# ── Main session ────────────────────────────────────────────────────────────

def receive_and_play(pc):
    os.makedirs(os.path.dirname(FIFO_PATH), exist_ok=True)
    if os.path.exists(FIFO_PATH):
        os.remove(FIFO_PATH)
    os.mkfifo(FIFO_PATH)

    console.print(f"[cyan]Connecting to {pc['name']} ({pc['ip']})...[/cyan]")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Low-latency socket: smaller buffer + no Nagle delay
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 64 * 1024)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    try:
        sock.connect((pc["ip"], pc["tcp_port"]))
    except (ConnectionRefusedError, OSError) as e:
        console.print(f"[red]Could not connect to {pc['ip']}:{pc['tcp_port']} -- {e}[/red]")
        console.print("[yellow]Make sure windows_sender.py is running and the firewall "
                       "allows TCP port 5005.[/yellow]")
        return

    result = read_header(sock)
    if result is None:
        console.print("[red]Connection closed before header was received[/red]")
        sock.close()
        return
    header, remainder = result
    samplerate = header["samplerate"]
    channels = header["channels"]
    console.print(f"[green]Stream: {samplerate} Hz, {channels}ch, s16le[/green]")

    write_cava_config(samplerate, channels)

    ensure_pulseaudio()
    time.sleep(0.3)

    os.makedirs(os.path.dirname(AUDIO_LOG_PATH), exist_ok=True)
    audio_log = open(AUDIO_LOG_PATH, "w")
    console.print(f"[dim]pacat log: {AUDIO_LOG_PATH}[/dim]")

    player = subprocess.Popen(
        ["pacat", "--format=s16le", f"--rate={samplerate}", f"--channels={channels}", "--latency-msec=20"],
        stdin=subprocess.PIPE,
        stdout=audio_log,
        stderr=subprocess.STDOUT,
    )

    # Check if pacat survived startup
    time.sleep(0.5)
    if player.poll() is not None:
        audio_log.close()
        console.print("[red]pacat exited immediately! Checking log...[/red]")
        try:
            with open(AUDIO_LOG_PATH) as f:
                log_content = f.read().strip()
            if log_content:
                console.print(f"[yellow]{log_content}[/yellow]")
            else:
                console.print("[yellow]pacat log is empty. PulseAudio may not be working.[/yellow]")
        except OSError:
            pass
        console.print("[yellow]Try: pulseaudio --start --load=module-sles-sink[/yellow]")
        sock.close()
        return

    # Start cava -- it opens the FIFO read end.  We give it /dev/null for
    # stdin so WE can own the real stdin for volume-key handling.
    cava_proc = None
    devnull = None
    try:
        devnull = open(os.devnull, "r")
        cava_proc = subprocess.Popen(["cava", "-p", CAVA_CONFIG_PATH], stdin=devnull)
    except FileNotFoundError:
        console.print("[yellow]cava not found -- audio will still play through speakers.[/yellow]")
        if devnull:
            devnull.close()
            devnull = None

    # Give cava a moment to open the FIFO read end
    time.sleep(0.5)

    stop_event = threading.Event()
    fifo_ready = threading.Event()
    reader_thread = threading.Thread(
        target=network_reader,
        args=(sock, remainder, player, fifo_ready, stop_event),
        daemon=True,
    )
    reader_thread.start()
    fifo_ready.wait(timeout=5.0)

    # ── Volume-control key loop ────────────────────────────────────────
    volume = 100
    set_volume(volume)
    update_title(pc["name"], volume)
    console.print("[dim]Controls: +/- volume, q quit[/dim]")

    old_settings = termios.tcgetattr(sys.stdin)
    try:
        tty.setcbreak(sys.stdin.fileno())

        while not stop_event.is_set():
            # If cava exited on its own, we're done
            if cava_proc is not None and cava_proc.poll() is not None:
                break
            # If no cava, exit when the reader finishes
            if cava_proc is None and not reader_thread.is_alive():
                break

            # Wait for a keypress (0.5 s timeout so we can check the flags)
            if select.select([sys.stdin], [], [], 0.5)[0]:
                ch = sys.stdin.read(1)
                if ch in ("+", "="):
                    volume = min(100, volume + 5)
                    set_volume(volume)
                    update_title(pc["name"], volume)
                elif ch == "-":
                    volume = max(0, volume - 5)
                    set_volume(volume)
                    update_title(pc["name"], volume)
                elif ch == "q":
                    break
    except KeyboardInterrupt:
        pass
    finally:
        # Restore terminal
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
        # Reset terminal title
        sys.stdout.write("\033]0;\007")
        sys.stdout.flush()

        stop_event.set()
        if cava_proc is not None and cava_proc.poll() is None:
            cava_proc.terminate()
            cava_proc.wait()
        if devnull:
            devnull.close()
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()
        try:
            player.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        player.terminate()
        reader_thread.join(timeout=2)
        try:
            audio_log.close()
        except OSError:
            pass
        if os.path.exists(FIFO_PATH):
            os.remove(FIFO_PATH)

    console.print("\n[cyan]Audio Router stopped.[/cyan]")


def main():
    console.print("[bold cyan]Scanning local network for PCs...[/bold cyan]")
    pcs = scan_for_pcs()
    if not pcs:
        console.print("[yellow]No PCs found via auto-discovery.[/yellow]")
        manual_ip = Prompt.ask(
            "Enter your PC's IP address manually (or 'q' to quit)"
        ).strip()
        if manual_ip.lower() == "q" or not manual_ip:
            sys.exit(1)
        manual_port = 5005
        pcs = [{"ip": manual_ip, "name": manual_ip, "tcp_port": manual_port}]
        pc = pcs[0]
    else:
        pc = choose_pc(pcs) if len(pcs) > 1 else pcs[0]
        if len(pcs) == 1:
            console.print(f"[green]Found: {pc['name']} ({pc['ip']})[/green]")
    receive_and_play(pc)


if __name__ == "__main__":
    main()
