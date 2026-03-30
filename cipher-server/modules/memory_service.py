"""
modules/memory_service.py – Servizio background di consolidamento memoria

Thread daemon sempre attivo. Viene alimentato da Brain.think() dopo ogni
scambio completo (messaggio utente + risposta). Analizza via LLM e salva
subito ciò che vale la pena ricordare a lungo termine.
"""

import json
import queue
import re
import threading
from typing import Optional

from rich.console import Console

console = Console()

_PROMPT = """Sei Cipher. Analizza questo scambio con Simone.

Identifica SOLO le informazioni che vale la pena salvare a lungo termine —
cose utili da ricordare nelle sessioni future.

Salva:
- Dati personali di Simone (lavoro, città, progetti, relazioni, salute)
- Preferenze o abitudini espresse
- Decisioni importanti prese insieme
- Contesto su progetti o situazioni in corso
- Fatti tecnici o info rilevanti condivisi da Simone
- Momenti emotivamente significativi

NON salvare:
- Conversazioni banali o di routine
- Cose già ovvie o precedentemente note
- Semplici domande senza contenuto durevole

Scambio:
Simone: {user_msg}
Cipher: {assistant_msg}

Rispondi SOLO con JSON (lista vuota se non c'è niente da salvare):
{{"save": [
  {{"type": "personal|preference|fact|episode", "key": "campo (opzionale)", "value": "cosa salvare"}}
]}}
Solo JSON, nessuna spiegazione."""


class MemoryService:
    def __init__(self, brain=None) -> None:
        self._brain   = brain
        self._queue:  queue.Queue = queue.Queue()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        console.print("[green]✓ MemoryService inizializzato[/green]")

    # ── Ciclo di vita ─────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread  = threading.Thread(
            target=self._loop, daemon=True, name="CipherMemoryService"
        )
        self._thread.start()
        console.print("[green]✓ MemoryService avviato[/green]")

    def stop(self) -> None:
        self._running = False
        self._queue.put(None)   # sblocca il thread in attesa

    # ── Alimentazione dalla conversazione ─────────────────────────────

    def feed(self, user_msg: str, assistant_msg: str) -> None:
        """Chiamato da Brain.think() dopo ogni scambio completo."""
        self._queue.put((user_msg, assistant_msg))

    # ── Loop ──────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while self._running:
            try:
                item = self._queue.get(timeout=5)
            except queue.Empty:
                continue

            if item is None:
                break

            user_msg, assistant_msg = item
            try:
                self._process(user_msg, assistant_msg)
            except Exception as e:
                console.print(f"[red]MemoryService errore: {e}[/red]")
            finally:
                self._queue.task_done()

    # ── Analisi e salvataggio ─────────────────────────────────────────

    def _process(self, user_msg: str, assistant_msg: str) -> None:
        if not self._brain:
            return

        result = self._brain._call_llm_silent(
            _PROMPT.format(
                user_msg=user_msg[:600],
                assistant_msg=assistant_msg[:600],
            )
        )
        if not result:
            return

        match = re.search(r'\{.*\}', result, re.DOTALL)
        if not match:
            return

        try:
            data = json.loads(match.group())
        except Exception:
            return

        saved = 0
        for item in data.get("save", []):
            item_type = item.get("type", "fact")
            key       = item.get("key", "")
            value     = item.get("value", "")
            if not value:
                continue

            if item_type == "episode":
                if self._brain._episodic_memory:
                    self._brain._episodic_memory.add_episode(
                        content=value,
                        episode_type="observation",
                        tags=["memory_service"],
                    )
                    saved += 1
            elif item_type in ("personal", "preference"):
                self._brain._memory.update_profile(key, value, category=item_type)
                saved += 1
            else:
                self._brain._memory.add_fact(value)
                saved += 1

        if saved:
            console.print(f"[dim]💾 MemoryService: {saved} elemento/i salvato/i[/dim]")
