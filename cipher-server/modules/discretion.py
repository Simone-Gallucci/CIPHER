"""
modules/discretion.py – Motore di discrezionalità di Cipher

Ogni volta che Cipher vuole contattare Simone spontaneamente,
questo motore decide: farlo adesso? aspettare? non farlo?

Criteri:
  - Ore silenziose (23:00–07:00): solo messaggi urgenti
  - Anti-spam: max 3 notifiche/ora, max 12/giorno
  - Urgenza: urgent / normal / low
  - Storico impatto: se un tipo di messaggio ha bassa efficacia, lo deprioritizza
  - Distanza dall'ultima notifica: rispetta il silenzio di Simone

Urgency levels:
  "urgent" – invia sempre (evento tra <30 min, email urgente, preoccupazione per Simone)
  "normal" – invia nelle ore attive, rispetta anti-spam
  "low"    – invia solo se Simone non è stato contattato nell'ultima ora e sono ore attive
"""

import json
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from rich.console import Console

from config import Config

console = Console()

# ── Configurazione orari ──────────────────────────────────────────────
QUIET_START = 23   # Inizio ore silenziose
QUIET_END   = 7    # Fine ore silenziose (esclusivo)

# ── Limiti anti-spam ──────────────────────────────────────────────────
MAX_PER_HOUR = 3
MAX_PER_DAY  = 12

# ── File di stato ─────────────────────────────────────────────────────
DISCRETION_FILE = Config.MEMORY_DIR / "discretion_state.json"


class DiscretionEngine:
    def __init__(self, impact_tracker=None):
        self._impact_tracker = impact_tracker
        self._state          = self._load()

    # ── Persistenza ───────────────────────────────────────────────────

    def _load(self) -> dict:
        if DISCRETION_FILE.exists():
            try:
                return json.loads(DISCRETION_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"sent_log": []}

    def _save(self):
        DISCRETION_FILE.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── Conteggi ──────────────────────────────────────────────────────

    def _recent_sent(self, within_minutes: int) -> list:
        """Ritorna i messaggi inviati nelle ultime N minuti."""
        cutoff = (datetime.now() - timedelta(minutes=within_minutes)).isoformat()
        return [
            e for e in self._state.get("sent_log", [])
            if e.get("timestamp", "") >= cutoff
        ]

    def _sent_today(self) -> list:
        today = datetime.now().date().isoformat()
        return [
            e for e in self._state.get("sent_log", [])
            if e.get("timestamp", "").startswith(today)
        ]

    def _last_sent_minutes_ago(self) -> Optional[float]:
        log = self._state.get("sent_log", [])
        if not log:
            return None
        last = log[-1].get("timestamp", "")
        try:
            delta = datetime.now() - datetime.fromisoformat(last)
            return delta.total_seconds() / 60
        except Exception:
            return None

    # ── Core judgment ─────────────────────────────────────────────────

    def should_send(
        self,
        action_type: str,
        content: str,
        urgency: str = "normal",
    ) -> tuple[bool, str]:
        """
        Decide se inviare la notifica adesso.

        Args:
            action_type: tipo di azione (checkin, news_shared, proactive_message, ecc.)
            content:     testo del messaggio
            urgency:     "urgent" | "normal" | "low"

        Returns:
            (True, motivo) se deve inviare
            (False, motivo) se deve aspettare o non inviare
        """
        now  = datetime.now()
        hour = now.hour

        # ── Urgente: passa sempre (tranne ore di notte profonda 01-06) ──
        if urgency == "urgent":
            if 1 <= hour < 6:
                return False, "Urgente ma sono le ore notturne profonde (01-06), aspetto le 6:00"
            return True, "Urgenza alta: invio immediato"

        # ── Ore silenziose ────────────────────────────────────────────
        in_quiet = (hour >= QUIET_START) or (hour < QUIET_END)
        if in_quiet:
            return False, f"Ore silenziose ({QUIET_START}:00–{QUIET_END}:00): solo messaggi urgenti"

        # ── Anti-spam orario ──────────────────────────────────────────
        last_hour_count = len(self._recent_sent(60))
        if last_hour_count >= MAX_PER_HOUR:
            return False, f"Anti-spam: già {last_hour_count} notifiche nell'ultima ora (max {MAX_PER_HOUR})"

        # ── Anti-spam giornaliero ─────────────────────────────────────
        today_count = len(self._sent_today())
        if today_count >= MAX_PER_DAY:
            return False, f"Anti-spam: già {today_count} notifiche oggi (max {MAX_PER_DAY})"

        # ── Priorità bassa: rispetta il silenzio ─────────────────────
        if urgency == "low":
            minutes_ago = self._last_sent_minutes_ago()
            if minutes_ago is not None and minutes_ago < 90:
                return False, f"Priorità bassa: ultima notifica {minutes_ago:.0f} min fa, aspetto 90 min"

        # ── Considera l'efficacia storica del tipo di azione ─────────
        if self._impact_tracker and urgency != "urgent":
            effectiveness = self._get_action_effectiveness(action_type)
            if effectiveness is not None and effectiveness < 0.2:
                return False, f"Efficacia storica bassa ({effectiveness:.0%}) per '{action_type}': salto"

        return True, "OK"

    def _get_action_effectiveness(self, action_type: str) -> Optional[float]:
        """Ritorna il tasso di positività storico per il tipo di azione. None se dati insufficienti."""
        if not self._impact_tracker:
            return None
        log = [
            e for e in self._impact_tracker._log[-100:]
            if e.get("action_type") == action_type and e.get("impact")
        ]
        if len(log) < 3:
            return None
        positive = sum(1 for e in log if e["impact"] == "positive")
        return positive / len(log)

    # ── Registrazione invio ───────────────────────────────────────────

    def record_sent(self, action_type: str, content: str):
        """Da chiamare dopo ogni invio effettivo."""
        entry = {
            "timestamp":   datetime.now().isoformat(),
            "action_type": action_type,
            "preview":     content[:80],
        }
        log = self._state.setdefault("sent_log", [])
        log.append(entry)
        # Tieni solo ultimi 7 giorni
        cutoff = (datetime.now() - timedelta(days=7)).isoformat()
        self._state["sent_log"] = [e for e in log if e.get("timestamp", "") >= cutoff]
        self._save()

    # ── Report leggibile ──────────────────────────────────────────────

    def status_report(self) -> str:
        last_hour = len(self._recent_sent(60))
        today     = len(self._sent_today())
        last_ago  = self._last_sent_minutes_ago()
        last_str  = f"{last_ago:.0f} min fa" if last_ago is not None else "mai"
        return (
            f"Discrezionalità:\n"
            f"  Inviate ultima ora: {last_hour}/{MAX_PER_HOUR}\n"
            f"  Inviate oggi: {today}/{MAX_PER_DAY}\n"
            f"  Ultima notifica: {last_str}"
        )
