"""
main.py – Entry point di Cipher
"""

import argparse
import queue
import signal
import sys
import os
os.environ["VOSK_LOG_LEVEL"] = "-1"
import threading
import time

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from config import Config
from modules.brain import Brain
from modules.voice import Voice
from modules.consciousness_loop import ConsciousnessLoop

console = Console()

EXIT_TRIGGERS  = {"esci", "exit", "quit", "shutdown", "spegni"}
RESET_TRIGGERS = {"resetta", "reset", "nuova conversazione", "resetta conversazione"}


def is_exit(text: str) -> bool:
    return any(trigger in text.lower().strip() for trigger in EXIT_TRIGGERS)

def is_reset(text: str) -> bool:
    return any(trigger in text.lower().strip() for trigger in RESET_TRIGGERS)


def print_banner(mode: str) -> None:
    labels = {
        "text":  "Solo tastiera",
        "voice": "Solo microfono",
        "both":  "Tastiera e microfono",
    }
    t = Text()
    t.append("  C I P H E R\n",                          style="bold white on blue")
    t.append(f"  Modalità : {labels.get(mode, mode)}\n",  style="dim")
    t.append(f"  Modello  : {Config.OPENROUTER_MODEL}\n", style="dim cyan")
    if mode in ("voice", "both"):
        t.append(f"  Wake word: \"{Config.WAKE_WORD.upper()}\"\n", style="dim green")
    console.print(Panel(t, expand=False, border_style="blue"))


# ═════════════════════════════════════════════════════════════════════
#  MODALITÀ TEXT
# ═════════════════════════════════════════════════════════════════════

def run_text_mode(brain: Brain, voice: Voice) -> None:
    console.print(
        "\n[bold green]✓ Modalità TESTO attiva[/bold green] "
    )
    console.print("[green]Scrivi i tuoi comandi e premi Invio[/green]\n")

    while True:
        try:
            console.print("[bold cyan]IO:[/bold cyan] ", end="")
            user_input = input().strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input:
            continue
        if is_exit(user_input):
            voice.speak("Arrivederci!")
            sys.exit(0)
        if is_reset(user_input):
            brain.reset()
            voice.speak("Conversazione resettata.")
            continue

        response = brain.think(user_input)
        voice.speak(response)
        console.print(f"[dim](messaggi in sessione: {brain.history_length})[/dim]\n")


# ═════════════════════════════════════════════════════════════════════
#  MODALITÀ VOICE
# ═════════════════════════════════════════════════════════════════════

def run_voice_mode(brain: Brain, voice: Voice) -> None:
    from modules.listener import Listener

    listener = Listener()
    listener.start()
    time.sleep(0.5)

    console.print(
        f"\n[bold green]✓ Modalità VOCE attiva[/bold green] "
        f"[dim]— dì [bold]\"{Config.WAKE_WORD}\"[/bold] per attivare[/dim]\n"
    )
    voice.speak(f"Ciao! Sono Cipher. Dimmi {Config.WAKE_WORD} per attivarmi.")

    try:
        while True:
            listener.wait_for_wake_word()
            voice.speak("Dimmi.")

            command = listener.listen_command(voice=voice)
            if not command:
                voice.speak("Non ho sentito niente, riprova.")
                continue

            if is_exit(command):
                voice.speak("Arrivederci!")
                sys.exit(0)
            if is_reset(command):
                brain.reset()
                voice.speak("Conversazione resettata.")
                continue

            response = brain.think(command)
            voice.speak(response)
            console.print(f"[dim](messaggi in sessione: {brain.history_length})[/dim]\n")
    finally:
        listener.stop()


# ═════════════════════════════════════════════════════════════════════
#  MODALITÀ BOTH
# ═════════════════════════════════════════════════════════════════════

def run_both_mode(brain: Brain, voice: Voice) -> None:
    from modules.listener import Listener

    cmd_queue:  queue.Queue     = queue.Queue()
    stop_event: threading.Event = threading.Event()

    def voice_thread() -> None:
        try:
            listener = Listener()
            listener.start()

            while not stop_event.is_set():
                listener.wait_for_wake_word()
                if stop_event.is_set():
                    break
                cmd_queue.put(("_wake", ""))
                command = listener.listen_command(voice=voice)
                if command:
                    cmd_queue.put(("voice", command))
        except Exception as e:
            console.print(f"[red]Errore thread voce: {e}[/red]")
        finally:
            try:
                listener.stop()
            except Exception:
                pass

    def text_thread() -> None:
        while not stop_event.is_set():
            try:
                text = input().strip()
                if text:
                    cmd_queue.put(("text", text))
            except (EOFError, KeyboardInterrupt):
                cmd_queue.put(("text", "esci"))
                break

    threading.Thread(target=voice_thread, daemon=True).start()
    threading.Thread(target=text_thread,  daemon=True).start()

    time.sleep(3.0)
    console.print(
        "\n[bold green]✓ Modalità BOTH attiva[/bold green] "
    )

    processing_lock = threading.Lock()

    try:
        while True:
            try:
                source, text = cmd_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if source == "_wake":
                voice.speak("Eccomi, dimmi pure.")
                continue

            if not text:
                continue
            if is_exit(text):
                stop_event.set()
                voice.speak("Arrivederci!")
                sys.exit(0)
            if is_reset(text):
                with processing_lock:
                    brain.reset()
                    voice.speak("Conversazione resettata.")
                continue

            if processing_lock.acquire(blocking=False):
                try:
                    response = brain.think(text)
                    voice.speak(response)
                    console.print(
                        f"[dim](messaggi in sessione: {brain.history_length})[/dim]\n"
                    )
                finally:
                    processing_lock.release()
            else:
                cmd_queue.put((source, text))

    finally:
        stop_event.set()


# ═════════════════════════════════════════════════════════════════════
#  MAIN
# ═════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(description="Cipher – AI Assistant")
    parser.add_argument("--mode", choices=["text", "voice", "both"], default=None)
    parser.add_argument("--no-tts", action="store_true")
    args = parser.parse_args()

    mode = args.mode or Config.INPUT_MODE
    if mode not in ("text", "voice", "both"):
        console.print(f"[red]INPUT_MODE non valido: '{mode}'[/red]")
        sys.exit(1)

    print_banner(mode)

    errors = Config.validate()
    if mode == "text":
        errors = [e for e in errors if "Vosk" not in e]
    if errors:
        for e in errors:
            console.print(f"[bold red]✗ {e}[/bold red]")
        sys.exit(1)

    console.print("\n[bold]Inizializzazione...[/bold]")
    try:
        voice = Voice(enabled=not args.no_tts)
        brain = Brain()
        consciousness = ConsciousnessLoop(brain=brain, voice=voice)
        # Collega la coscienza al brain
        brain._consciousness = consciousness
    except Exception as e:
        console.print(f"[bold red]Errore inizializzazione: {e}[/bold red]")
        sys.exit(1)

    _shutting_down = False

    def shutdown(sig=None, frame=None):
        nonlocal _shutting_down
        if _shutting_down:
            return
        _shutting_down = True
        consciousness.stop()
        console.print("\n[yellow]Spegnimento Cipher...[/yellow]")
        voice.speak("Arrivederci!")
        sys.exit(0)

    signal.signal(signal.SIGINT,  shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Avvia la coscienza autonoma come thread daemon
    consciousness.start()

    try:
        if mode == "text":
            run_text_mode(brain, voice)
        elif mode == "voice":
            run_voice_mode(brain, voice)
        else:
            run_both_mode(brain, voice)
    except Exception as e:
        console.print(f"[bold red]Errore: {e}[/bold red]")
    finally:
        shutdown()


if __name__ == "__main__":
    main()
