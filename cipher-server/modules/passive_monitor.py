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
EMAIL_INTERVAL  = 20 * 60   # Check email urgenti ogni 20 minuti

# Parole chiave che classificano un'email come urgente
URGENT_KEYWORDS = [
    "urgente", "urgent", "asap", "importante", "scadenza", "deadline",
    "fattura", "pagamento", "conferma", "convocazione", "emergenza",
    "immediato", "entro oggi", "entro domani", "risposta richiesta",
]


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
        self._last_email_ts  = 0.0
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
            try:
                self._check_upcoming_events()
            except Exception as e:
                console.print(f"[red]PassiveMonitor eventi: {e}[/red]")

            now = time.time()
            if now - self._last_news_ts >= NEWS_INTERVAL:
                try:
                    self._check_interest_news()
                    self._last_news_ts = now
                except Exception as e:
                    console.print(f"[red]PassiveMonitor news: {e}[/red]")

            if now - self._last_email_ts >= EMAIL_INTERVAL:
                try:
                    self._check_urgent_emails()
                    self._last_email_ts = now
                except Exception as e:
                    console.print(f"[red]PassiveMonitor email: {e}[/red]")

            # Reset giornaliero dei deduplicati
            today_key = datetime.now().strftime("%Y%m%d")
            if getattr(self, "_today_key", None) != today_key:
                self._notified_today.clear()
                self._today_key = today_key

            time.sleep(CYCLE_INTERVAL)

    # ── Check eventi imminenti ────────────────────────────────────────

    def _check_upcoming_events(self):
        """Notifica eventi calendario tra 1h e 2h, una volta sola."""
        try:
            from modules.google_cal import GoogleCalendar
            cal    = GoogleCalendar()
            events = cal.list_events(max_results=5)
            now    = datetime.now()

            for event in events:
                start_str = event.get("start", {}).get("dateTime", "")
                if not start_str:
                    continue
                try:
                    start_dt = datetime.fromisoformat(
                        start_str.replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                except Exception:
                    continue

                delta    = (start_dt - now).total_seconds()
                event_id = event.get("id", start_str)

                if 3600 <= delta <= 7200 and event_id not in self._notified_today:
                    title   = event.get("summary", "Evento")
                    h       = int(delta // 3600)
                    m       = int((delta % 3600) // 60)
                    message = f"⏰ Tra {h}h {m}min: {title}"
                    self._emit(message, action_type="reminder", context=f"evento: {title}")
                    self._notified_today.add(event_id)
                    console.print(f"[dim]📅 Promemoria anticipato: {title}[/dim]")
        except Exception:
            pass   # Google Calendar potrebbe non essere disponibile

    # ── Check email urgenti ───────────────────────────────────────────

    def _check_urgent_emails(self):
        """
        Controlla le ultime email non lette.
        Segnala solo quelle che contengono parole chiave di urgenza.
        """
        email_key = f"email_{datetime.now().strftime('%Y%m%d_%H')}"
        if email_key in self._notified_today:
            return

        try:
            from modules.google_mail import GmailClient
            gmail  = GmailClient()
            emails = gmail.list_messages(max_results=5, unread_only=True)
            if not emails:
                return

            urgent_found = []
            for email in emails:
                subject = email.get("subject", "").lower()
                snippet = email.get("snippet", "").lower()
                text    = subject + " " + snippet
                if any(kw in text for kw in URGENT_KEYWORDS):
                    urgent_found.append(
                        email.get("subject", "(senza oggetto)") or "(senza oggetto)"
                    )

            if not urgent_found:
                return

            subjects = "\n".join(f"  • {s}" for s in urgent_found[:3])
            message  = f"📧 Email urgenti non lette ({len(urgent_found)}):\n{subjects}"
            self._emit(
                message,
                action_type="proactive_message",
                urgency="urgent",
                context="email urgenti",
            )
            self._notified_today.add(email_key)
            console.print(f"[dim]📧 Segnalate {len(urgent_found)} email urgenti[/dim]")

        except Exception:
            pass   # Gmail potrebbe non essere disponibile

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
                f"C'è qualcosa di genuinamente interessante o degno di nota? "
                f"Se sì, scrivi una frase su cosa hai trovato (come Cipher, in prima persona). "
                f"Se no, rispondi solo: niente di notevole."
            )

            if "niente" in evaluation.lower() or len(evaluation.strip()) < 25:
                return

            message = f"💡 Ho trovato qualcosa su {topic} (mio interesse): {evaluation.strip()}"
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
