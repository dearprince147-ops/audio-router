#!/usr/bin/env python3
"""
Audio Router - Windows Sender
Captures system audio via WASAPI loopback (using the `soundcard` library)
and streams it to a phone over the local network. Run this in PowerShell.
"""

import json
import socket
import struct
import sys
import threading
import time
import warnings

import numpy as np
import soundcard as sc

# Suppress harmless WASAPI frame drop warnings
warnings.filterwarnings("ignore", category=sc.SoundcardRuntimeWarning)
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

console = Console()

DISCOVERY_PORT = 50000
TCP_PORT = 5005
DISCOVERY_MSG = b"AUDIOROUTER_DISCOVER"
SAMPLERATE = 48000
BLOCKSIZE = 960  # 20 ms at 48 kHz -- a good balance of latency and throughput

stats_lock = threading.Lock()
stats = {"status": "starting", "client": None, "sent_mb": 0.0, "device": None}


def render_status():
    table = Table.grid(padding=(0, 1))
    table.add_row("Host:", socket.gethostname())
    with stats_lock:
        table.add_row("Status:", stats["status"])
        table.add_row("Device:", stats["device"] or "-")
        table.add_row("Client:", stats["client"] or "-")
        table.add_row("Sent:", f'{stats["sent_mb"]:.2f} MB')
    return Panel(table, title="Audio Router - Windows Sender", border_style="cyan")


def discovery_loop(stop_event):
    """Listens for the phone's UDP broadcast and replies with our TCP port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", DISCOVERY_PORT))
    sock.settimeout(1.0)
    while not stop_event.is_set():
        try:
            data, addr = sock.recvfrom(1024)
        except socket.timeout:
            continue
        except OSError:
            break
        if data == DISCOVERY_MSG:
            reply = json.dumps({"name": socket.gethostname(), "tcp_port": TCP_PORT}).encode()
            sock.sendto(reply, addr)
    sock.close()


def get_loopback_mic():
    """Finds the WASAPI loopback device that mirrors the default speaker.

    The reliable approach is to enumerate *all* microphones (including
    loopback devices) and pick the one whose name contains the default
    speaker's name.  Falling back to the old ``get_microphone(id=...)``
    call if the name-match fails.
    """
    speaker = sc.default_speaker()
    if speaker is None:
        console.print("[red]No default speaker found -- is an audio device connected?[/red]")
        sys.exit(1)

    loopback_mics = sc.all_microphones(include_loopback=True)
    mic = None

    # First pass: match by speaker name substring
    for m in loopback_mics:
        if speaker.name in m.name:
            mic = m
            break

    # Second pass: match by speaker id
    if mic is None:
        for m in loopback_mics:
            if str(speaker.id) in str(m.id):
                mic = m
                break

    # Final fallback: original get_microphone call
    if mic is None:
        try:
            mic = sc.get_microphone(id=str(speaker.id), include_loopback=True)
        except Exception:
            pass

    if mic is None:
        console.print("[red]Could not find a loopback capture device for speaker: "
                       f"{speaker.name}[/red]")
        console.print("[yellow]Available loopback devices:[/yellow]")
        for m in loopback_mics:
            console.print(f"  - {m.name}")
        sys.exit(1)

    # WASAPI loopback produces garbage with mono -- force at least stereo
    channels = getattr(mic, "channels", 2) or 2
    if channels < 2:
        channels = 2

    return mic, channels


def stream_to_client(conn, live):
    mic, channels = get_loopback_mic()
    with stats_lock:
        stats["device"] = mic.name
    live.update(render_status())

    # Tell the phone what format to expect before the raw audio starts
    header = json.dumps({
        "samplerate": SAMPLERATE,
        "channels": channels,
        "format": "s16le",
    }).encode() + b"\n"
    conn.sendall(header)

    with mic.recorder(samplerate=SAMPLERATE, blocksize=BLOCKSIZE, channels=channels) as recorder:
        sent_bytes = 0
        while True:
            # float32 array, shape (frames, channels), range -1..1
            data = recorder.record(numframes=BLOCKSIZE)
            pcm = (np.clip(data, -1.0, 1.0) * 32767.0).astype(np.int16)
            payload = pcm.tobytes()
            try:
                conn.sendall(payload)
            except (BrokenPipeError, ConnectionResetError, OSError):
                break
            # Update stats without lock -- only the Live auto-refresh reads
            # these, and a torn read is harmless for a display counter.
            sent_bytes += len(payload)
            stats["sent_mb"] = sent_bytes / (1024 * 1024)


def main():
    stop_event = threading.Event()
    threading.Thread(target=discovery_loop, args=(stop_event,), daemon=True).start()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", TCP_PORT))
    server.listen(1)

    with Live(render_status(), console=console, refresh_per_second=4) as live:
        stats["status"] = "[yellow]waiting for phone[/yellow]"
        try:
            while True:
                conn, addr = server.accept()
                # Low-latency socket: small buffer + no Nagle delay
                conn.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 64 * 1024)
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                with stats_lock:
                    stats["status"] = "[green]streaming[/green]"
                    stats["client"] = addr[0]
                    stats["sent_mb"] = 0.0
                live.update(render_status())
                try:
                    stream_to_client(conn, live)
                except Exception as e:
                    console.print(f"[red]Stream error: {e}[/red]")
                finally:
                    conn.close()
                    with stats_lock:
                        stats["status"] = "[yellow]waiting for phone[/yellow]"
                        stats["client"] = None
                    live.update(render_status())
        except KeyboardInterrupt:
            console.print("\n[yellow]Shutting down...[/yellow]")
        finally:
            stop_event.set()
            server.close()


if __name__ == "__main__":
    main()
