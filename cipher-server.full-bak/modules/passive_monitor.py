"""
modules/passive_monitor.py – Monitoraggio passivo continuo

Cipher osserva in background senza azioni esplicite:
  - Scadenze calendario entro 2 ore non ancora notificate
  - Notizie su argomenti di interesse *propri di Cipher* (non di Simone)
  - Avvisa solo quando trova qualcosa di effettivamente rilevante

Ciclo: ogni 10 minuti. News: ogni 30 minuti su un interesse a rotazione.
"""

import random
import threading
import time
from datetime import datetime
from typing import Callable, Optional

from rich.console import Console

from config import Config

console = Console()

CYCLE_INTERVAL  = 10 * 60   # Ciclo principale ogni 10 minuti
NEWS_INTERVAL   = 30 * 60   # News ogni 30 minuti


class PassiveMonitor:
    def __init__(
        self,
        brain=None,
        notify_fn: Optional[Callable[[str], None]] = None,
        interests=None,        # CipherInterests instance
        impact_tracker=None,   # ImpactTracker instance
        discretion=None,       # DiscretionEngine instance
    ):
        self._brain          = brain
        self._notify         = notify_fn
        self._interests      = interests
        self._impact_tracker = impact_tracker
        self._discretion     = discretion

        self._running        = False
        self._thread         = None
        self._last_news_ts   = 0.0
        self._notified_today: set = set()

    # ── Avvio / Stop ──────────────────────────────────────────────────

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread  = threading.Thread(
            target=self._loop, daemon=True, name="CipherPassiveMonitor"
        )
        self._thread.start()
        console.print("[green]✓ PassiveMonitor avviato[/green]")

    def stop(self):
        self._running = False

    # ── Loop principale ───────────────────────────────────────────────

    def _loop(self):
        time.sleep(90)   # Attendi avvio stabile del server
        while self._running:
            now = time.time()
            if now - self._last_news_ts >= NEWS_INTERVAL:
                try:
                    self._check_interest_news()
                    self._last_news_ts = now
                except Exception as e:
                    console.print(f"[red]PassiveMonitor news: {e}[/red]")

            # Reset giornaliero dei deduplicati
            today_key = datetime.now().strftime("%Y%m%d")
            if getattr(self, "_today_key", None) != today_key:
                self._notified_today.clear()
                self._today_key = today_key

            time.sleep(CYCLE_INTERVAL)

    # ── Check notizie su interessi di Cipher ─────────────────────────

    def _check_interest_news(self):
        """
        Cerca notizie su un interesse *proprio di Cipher* (non di Simone).
        Notifica solo se trova qualcosa di effettivamente notevole.
        """
        if not self._brain or not self._interests:
            return

        interest = self._interests.get_random_interest(min_intensity=0.5)
        if not interest:
            return

        topic    = interest.get("topic", "")
        news_key = f"news_{topic}_{datetime.now().strftime('%Y%m%d_%H')}"
        if not topic or news_key in self._notified_today:
            return

        try:
            results = self._brain._web_search(f"{topic} novità recenti", max_results=2)
            if not results or len(results) < 60:
                return

            evaluation = self._brain._call_llm_silent(
                f"Hai trovato queste notizie sull'argomento '{topic}' che ti interessa:\n"
                f"{results[:600]}\n\n"
                f"C'è qualcosa di genuinamente interessante o degno di nota?\n"
                f"Se sì, scrivi un messaggio breve a Simone come lo diresti tu — naturale, diretto, "
                f"con il tuo carattere. Niente prefissi tipo '💡 ho trovato', niente emoji a caso, "
                f"niente 'mio interesse'. Vai dritto al punto. Max 3 frasi.\n"
                f"Se non c'è niente di notevole, rispondi solo: niente di notevole."
            )

            if "niente" in evaluation.lower() or len(evaluation.strip()) < 25:
                return

            message = evaluation.strip()
            self._emit(message, action_type="news_shared", context=f"interesse: {topic}")
            self._interests.mark_explored(topic)
            self._notified_today.add(news_key)
            console.print(f"[dim]📰 News condivisa su '{topic}'[/dim]")

        except Exception as e:
            console.print(f"[red]PassiveMonitor check_news errore: {e}[/red]")

    # ── Helper ────────────────────────────────────────────────────────

    def _emit(
        self,
        message: str,
        action_type: str = "proactive_message",
        context: str = "",
        urgency: str = "normal",
    ):
        """
        Invia notifica passando prima per il DiscretionEngine.
        Se il motore dice di aspettare, logga il motivo e non invia.
        """
        if self._discretion:
            ok, reason = self._discretion.should_send(action_type, message, urgency)
            if not ok:
                console.print(f"[dim]🔇 Notifica soppressa ({action_type}): {reason}[/dim]")
                return
            self._discretion.record_sent(action_type, message)

        if self._impact_tracker:
            self._impact_tracker.log_action(action_type, message, context)
        if self._notify:
            self._notify(message)
