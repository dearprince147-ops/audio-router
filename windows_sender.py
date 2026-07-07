import socket
import sounddevice as sd
import numpy as np
from rich.console import Console
from rich.progress import Progress

console = Console()
BUFFER_SIZE = 4096
SAMPLE_RATE = 44100

def discover_listener():
    # Listens for phone's broadcast, sends back PC name
    # Returns phone's IP and Port
    pass

def stream_audio(phone_ip, phone_port):
    console.print("[bold green]Connected to LG V30![/bold green]")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((phone_ip, phone_port))
    
    with Progress() as progress:
        task = progress.add_task("[cyan]Streaming audio...", total=100)
        
        # Capture system audio (Loopback)
        def callback(indata, frames, time, status):
            if status:
                console.print(f"[red]{status}[/red]")
            # Convert to bytes and send
            sock.sendall(indata.tobytes())
            progress.update(task, advance=0.5) # Fake progress for visual flair

        with sd.RawStream(samplerate=SAMPLE_RATE, channels=2, dtype='int16',
                          callback=callback, blocksize=BUFFER_SIZE):
            sd.sleep(1000000) # Keep stream alive

if __name__ == "__main__":
    console.print("[bold yellow]Waiting for LG V30 to connect...[/bold yellow]")
    phone_info = discover_listener()
    stream_audio(phone_info['ip'], phone_info['port'])
