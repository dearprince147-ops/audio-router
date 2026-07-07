#!/usr/bin/env python3
"""
Audio Router - Termux Receiver
Discovers PCs on the local network, connects to the one you pick, plays
the incoming audio through the phone's speakers, and drives cava full-screen.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time

from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

console = Console()

DISCOVERY_PORT = 50000
DISCOVERY_MSG = b"AUDIOROUTER_DISCOVER"
FIFO_PATH = os.path.expanduser("~/.cache/audiorouter/cava.fifo")
CAVA_CONFIG_PATH = os.path.expanduser("~/.cache/audiorouter/cava_audiorouter.conf")
FFPLAY_LOG_PATH = os.path.expanduser("~/.cache/audiorouter/ffplay.log")


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
            f"gradient_color_1 = '#0ff'\n"
            f"gradient_color_2 = '#06f'\n"
            f"gradient_color_3 = '#f0f'\n"
            f"gradient_color_4 = '#f06'\n"
            f"gradient_color_5 = '#ff0'\n"
            f"gradient_color_6 = '#0f0'\n"
            f"\n"
            f"[smoothing]\n"
            f"noise_reduction = 77\n"
        )


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


def network_reader(sock, initial_data, player, fifo_ready, stop_event):
    """Reads audio bytes from the PC and fans them out to ffplay (speakers)
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
        # Write any leftover data from the header read
        if initial_data:
            try:
                player.stdin.write(initial_data)
                player.stdin.flush()
            except (BrokenPipeError, OSError):
                return
            if fifo_fd is not None:
                try:
                    os.write(fifo_fd, initial_data)
                except OSError:
                    pass

        sock.settimeout(5.0)
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
                player.stdin.write(data)
                player.stdin.flush()
            except (BrokenPipeError, OSError):
                break
            if fifo_fd is not None:
                try:
                    os.write(fifo_fd, data)
                except OSError:
                    pass  # cava closed or pipe full -- non-fatal
    finally:
        if fifo_fd is not None:
            os.close(fifo_fd)


def receive_and_play(pc):
    os.makedirs(os.path.dirname(FIFO_PATH), exist_ok=True)
    if os.path.exists(FIFO_PATH):
        os.remove(FIFO_PATH)
    os.mkfifo(FIFO_PATH)

    console.print(f"[cyan]Connecting to {pc['name']} ({pc['ip']})...[/cyan]")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 256 * 1024)
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

    env = os.environ.copy()
    env.setdefault("SDL_AUDIODRIVER", "pulseaudio")

    os.makedirs(os.path.dirname(FFPLAY_LOG_PATH), exist_ok=True)
    ffplay_log = open(FFPLAY_LOG_PATH, "w")
    console.print(f"[dim]ffplay log: {FFPLAY_LOG_PATH}[/dim]")

    player = subprocess.Popen(
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "warning",
         "-f", "s16le", "-ar", str(samplerate), "-ac", str(channels), "-"],
        stdin=subprocess.PIPE,
        stdout=ffplay_log,
        stderr=subprocess.STDOUT,
        env=env,
    )

    stop_event = threading.Event()
    fifo_ready = threading.Event()
    reader_thread = threading.Thread(
        target=network_reader,
        args=(sock, remainder, player, fifo_ready, stop_event),
        daemon=True,
    )
    reader_thread.start()
    fifo_ready.wait(timeout=5.0)

    try:
        cava_proc = subprocess.Popen(["cava", "-p", CAVA_CONFIG_PATH])
        cava_proc.wait()  # cava takes over the terminal until you quit (q)
    except FileNotFoundError:
        console.print("[yellow]cava not found -- audio is still playing through speakers.[/yellow]")
        console.print("[yellow]Press Ctrl+C to stop.[/yellow]")
        try:
            reader_thread.join()
        except KeyboardInterrupt:
            pass
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
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
            ffplay_log.close()
        except OSError:
            pass
        if os.path.exists(FIFO_PATH):
            os.remove(FIFO_PATH)


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
