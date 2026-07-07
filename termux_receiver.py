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
CAVA_CONFIG_PATH = os.path.expanduser("~/.config/cava/config")


def ensure_cava_config():
    os.makedirs(os.path.dirname(CAVA_CONFIG_PATH), exist_ok=True)
    if not os.path.exists(CAVA_CONFIG_PATH):
        with open(CAVA_CONFIG_PATH, "w") as f:
            f.write(f"[input]\nmethod = fifo\nsource = {FIFO_PATH}\n\n[output]\nchannels = stereo\n")


def scan_for_pcs(timeout=3.0):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(0.5)
    sock.sendto(DISCOVERY_MSG, ("255.255.255.255", DISCOVERY_PORT))

    found = {}
    end_time = time.time() + timeout
    while time.time() < end_time:
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


def network_reader(sock, player, fifo_ready, stop_event):
    """Reads audio bytes from the PC and fans them out to ffplay (speakers)
    and to the cava FIFO (visualizer) at the same time."""
    fifo_fd = None
    for _ in range(30):
        if stop_event.is_set():
            return
        try:
            fifo_fd = os.open(FIFO_PATH, os.O_WRONLY | os.O_NONBLOCK)
            break
        except OSError:
            time.sleep(0.1)
    fifo_ready.set()
    if fifo_fd is None:
        console.print("[yellow]cava did not attach to the audio feed in time; visualizer may stay blank[/yellow]")

    try:
        while not stop_event.is_set():
            data = sock.recv(4096)
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
                    pass
    finally:
        if fifo_fd is not None:
            os.close(fifo_fd)


def receive_and_play(pc):
    os.makedirs(os.path.dirname(FIFO_PATH), exist_ok=True)
    if os.path.exists(FIFO_PATH):
        os.remove(FIFO_PATH)
    os.mkfifo(FIFO_PATH)
    ensure_cava_config()

    console.print(f"[cyan]Connecting to {pc['name']} ({pc['ip']})...[/cyan]")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((pc["ip"], pc["tcp_port"]))

    header_bytes = b""
    while not header_bytes.endswith(b"\n"):
        chunk = sock.recv(1)
        if not chunk:
            console.print("[red]Connection closed before header was received[/red]")
            return
        header_bytes += chunk
    header = json.loads(header_bytes.decode())
    samplerate, channels = header["samplerate"], header["channels"]
    console.print(f"[green]Stream format: {samplerate} Hz, {channels}ch -- launching cava[/green]")
    time.sleep(0.8)

    player = subprocess.Popen(
        ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet",
         "-f", "s16le", "-ar", str(samplerate), "-ac", str(channels), "-"],
        stdin=subprocess.PIPE,
    )

    stop_event = threading.Event()
    fifo_ready = threading.Event()
    reader_thread = threading.Thread(
        target=network_reader, args=(sock, player, fifo_ready, stop_event), daemon=True
    )
    reader_thread.start()
    fifo_ready.wait(timeout=3.5)

    try:
        cava_proc = subprocess.Popen(["cava"])
        cava_proc.wait()  # cava takes over the full screen until you quit it (q)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()
        player.terminate()
        reader_thread.join(timeout=2)
        if os.path.exists(FIFO_PATH):
            os.remove(FIFO_PATH)


def main():
    console.print("[bold cyan]Scanning local network for PCs...[/bold cyan]")
    pcs = scan_for_pcs()
    if not pcs:
        console.print("[red]No PCs found. Make sure windows_sender.py is running on your PC "
                       "and both devices are on the same Wi-Fi network.[/red]")
        sys.exit(1)
    pc = choose_pc(pcs)
    receive_and_play(pc)


if __name__ == "__main__":
    main()
