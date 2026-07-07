#!/usr/bin/env python3
"""
Audio Router - Windows Sender
Captures system audio via WASAPI loopback (using the `soundcard` library)
and streams it to a phone over the local network. Run this in PowerShell.
"""

import json
import socket
import threading

import numpy as np
import soundcard as sc
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

console = Console()

DISCOVERY_PORT = 50000
TCP_PORT = 5005
DISCOVERY_MSG = b"AUDIOROUTER_DISCOVER"
SAMPLERATE = 48000
BLOCKSIZE = 1024

stats_lock = threading.Lock()
stats = {"status": "starting", "client": None, "sent_mb": 0.0}


def render_status():
    table = Table.grid(padding=(0, 1))
    table.add_row("Host:", socket.gethostname())
    with stats_lock:
        table.add_row("Status:", stats["status"])
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
    """Gets a "microphone" that is actually the default speaker's loopback
    tap -- this is how `soundcard` captures whatever is currently playing."""
    speaker = sc.default_speaker()
    mic = sc.get_microphone(id=str(speaker.id), include_loopback=True)
    channels = getattr(mic, "channels", 2) or 2
    return mic, channels


def stream_to_client(conn, live):
    mic, channels = get_loopback_mic()

    # Tell the phone what format to expect before the raw audio starts
    header = json.dumps({"samplerate": SAMPLERATE, "channels": channels, "format": "s16le"}).encode() + b"\n"
    conn.sendall(header)

    with mic.recorder(samplerate=SAMPLERATE, blocksize=BLOCKSIZE) as recorder:
        while True:
            data = recorder.record(numframes=BLOCKSIZE)  # float32, shape (frames, channels), range -1..1
            pcm = (np.clip(data, -1.0, 1.0) * 32767.0).astype(np.int16)
            payload = pcm.tobytes()
            try:
                conn.sendall(payload)
            except (BrokenPipeError, ConnectionResetError, OSError):
                break
            with stats_lock:
                stats["sent_mb"] += len(payload) / (1024 * 1024)
            live.update(render_status())


def main():
    stop_event = threading.Event()
    threading.Thread(target=discovery_loop, args=(stop_event,), daemon=True).start()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", TCP_PORT))
    server.listen(1)

    with Live(render_status(), console=console, refresh_per_second=4) as live:
        with stats_lock:
            stats["status"] = "waiting for phone"
        live.update(render_status())
        try:
            while True:
                conn, addr = server.accept()
                with stats_lock:
                    stats["status"] = "streaming"
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
                        stats["status"] = "waiting for phone"
                        stats["client"] = None
                    live.update(render_status())
        except KeyboardInterrupt:
            console.print("\n[yellow]Shutting down...[/yellow]")
        finally:
            stop_event.set()
            server.close()


if __name__ == "__main__":
    main()
