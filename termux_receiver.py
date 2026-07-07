import socket
import os
import subprocess
import threading
from rich.console import Console
from rich.prompt import Prompt
from rich.progress import Progress

console = Console()
FIFO_PATH = "/data/data/com.termux/files/home/audio_fifo"

def scan_for_pcs():
    # Send UDP Broadcast, wait for replies
    # Return list of found PCs: [{"name": "Desktop", "ip": "192.168.1.5"}]
    pass

def start_cava():
    # Create FIFO if it doesn't exist
    if not os.path.exists(FIFO_PATH):
        os.mkfifo(FIFO_PATH)
    
    # Run cava in the background, reading from the FIFO
    # cava config needs: input method = fifo, fifo path = FIFO_PATH
    subprocess.Popen(["cava"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def receive_and_play(pc_ip):
    # Connect to PC
    # Start TCP Server to receive audio
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("0.0.0.0", 5005))
    server.listen(1)
    conn, addr = server.accept()
    
    # Open FIFO for writing, and ffplay for audio output
    fifo = open(FIFO_PATH, 'wb')
    # ffplay plays the raw audio to the phone speakers
    player = subprocess.Popen(["ffplay", "-nodisp", "-autoexit", "-f", "s16le", "-ar", "44100", "-ac", "2", "-"], stdin=subprocess.PIPE)
    
    with Progress() as progress:
        task = progress.add_task("[magenta]Receiving audio...", total=100)
        while True:
            data = conn.recv(4096)
            if not data:
                break
            fifo.write(data)   # Send to Cava
            player.stdin.write(data) # Send to Speakers
            progress.update(task, advance=0.5)

if __name__ == "__main__":
    console.print("[bold cyan]Scanning local network for PCs...[/bold cyan]")
    pcs = scan_for_pcs()
    
    # Display TUI Menu
    for i, pc in enumerate(pcs):
        console.print(f"[{i}] {pc['name']} ({pc['ip']})")
    
    choice = Prompt.ask("Select PC to connect")
    selected_pc = pcs[int(choice)]
    
    # Start Cava in background
    threading.Thread(target=start_cava, daemon=True).start()
    
    # Start receiving
    receive_and_play(selected_pc['ip'])
