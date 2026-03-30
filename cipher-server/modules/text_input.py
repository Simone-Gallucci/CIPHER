"""
modules/text_input.py – Input da riga di comando
Usato in modalità "text" e "both".
"""

import queue
import threading
from rich.console import Console

console = Console()


class TextInput:
    """
    Legge input da tastiera e lo inserisce in una coda thread-safe.
    In modalità "both" condivide la stessa coda con il Listener vocale.
    """

    def __init__(self, shared_queue: queue.Queue) -> None:
        self._queue   = shared_queue
        self._running = False
        self._thread: threading.Thread | None = None
        console.print("[green]✓ Input testuale pronto[/green]")

    def start(self) -> None:
        self._running = True
        self._thread  = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            try:
                text = input()
                if text.strip():
                    self._queue.put(("text", text.strip()))
            except (EOFError, KeyboardInterrupt):
                self._queue.put(("text", "esci"))
                break
