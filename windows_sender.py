#!/usr/bin/env python3
"""
Audio Router - Windows Sender
Captures system audio via WASAPI loopback and streams it to a phone
over the local network. Run this in PowerShell.
"""

import json
import socket
import threading
import time

import sounddevice as sd
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

console = Console()

DISCOVERY_PORT = 50000
TCP_PORT = 5005
DISCOVERY_MSG = b"AUDIOROUTER_DISCOVER"
CHUNK_FRAMES = 1024

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


def get_wasapi_loopback_device():
    """Finds the default output device on the WASAPI host API, which we
    open as an INPUT stream (loopback) to capture whatever is playing."""
    hostapis = sd.query_hostapis()
    wasapi = next((api for api in hostapis if "WASAPI" in api["name"]), None)
    if wasapi is None:
        raise RuntimeError("Windows WASAPI host API was not found (are you on Windows?)")
    device_index = wasapi["default_output_device"]
    if device_index is None or device_index < 0:
        raise RuntimeError("No default output device found for WASAPI")
    device_info = sd.query_devices(device_index)
    samplerate = int(device_info["default_samplerate"])
    channels = min(int(device_info["max_output_channels"]), 2) or 2
    return device_index, samplerate, channels


def stream_to_client(conn, live):
    device_index, samplerate, channels = get_wasapi_loopback_device()

    # Tell the phone what format to expect before the raw audio starts
    header = json.dumps({"samplerate": samplerate, "channels": channels, "format": "s16le"}).encode() + b"\n"
    conn.sendall(header)

    disconnected = threading.Event()

    def callback(indata, frames, time_info, status):
        try:
            data = indata.tobytes()
            conn.sendall(data)
            with stats_lock:
                stats["sent_mb"] += len(data) / (1024 * 1024)
        except (BrokenPipeError, ConnectionResetError, OSError):
            disconnected.set()
            raise sd.CallbackStop

    stream = sd.InputStream(
        samplerate=samplerate,
        device=device_index,
        channels=channels,
        dtype="int16",
        blocksize=CHUNK_FRAMES,
        callback=callback,
        extra_settings=sd.WasapiSettings(loopback=True),
    )
    with stream:
        while stream.active and not disconnected.is_set():
            time.sleep(0.2)
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
