"""
modules/pattern_learner.py – Traccia orari di attività e pattern di messaggi

Registra per ogni giornata: ore in cui Simone ha scritto e lunghezza dei messaggi.
Ogni 10 messaggi ricalcola il riepilogo (avg_message_length, most_active_hour, active_days).
I dati vivono in data/patterns.json — permanente, cancellato solo da Tabula Rasa.
"""

import copy
import json
import logging
from datetime import datetime
from pathlib import Path
from config import Config
from modules.utils import write_json_atomic

log = logging.getLogger("cipher.pattern_learner")

PATTERNS_FILE: Path = Config.DATA_DIR / "patterns.json"

EMPTY_PATTERNS: dict = {
    # "2026-04-10": {"hours": [9, 14, 21], "message_lengths": [45, 120, 33], "count": 3}
    "daily": {},
    # Riepilogo ricalcolato ogni 10 messaggi
    "summary": {},
}


class PatternLearner:
    """Traccia orari di attività e lunghezza messaggi di Simone."""

    def __init__(self, brain=None):
        self._brain = brain
        self._data: dict = self._load()
        self._msg_count_since_summary: int = 0

    # ──────────────────────────────────────────────────────────────────────
    #  Persistenza
    # ──────────────────────────────────────────────────────────────────────

    def _load(self) -> dict:
        if PATTERNS_FILE.exists():
            try:
                return json.loads(PATTERNS_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return copy.deepcopy(EMPTY_PATTERNS)

    def _save(self) -> None:
        try:
            write_json_atomic(PATTERNS_FILE, self._data)
        except Exception as e:
            log.warning("PatternLearner save error: %s", e)

    # ──────────────────────────────────────────────────────────────────────
    #  Registrazione
    # ──────────────────────────────────────────────────────────────────────

    def record_message(self, text: str) -> None:
        """Registra ora del giorno e lunghezza del messaggio per la giornata corrente."""
        now   = datetime.now()
        today = now.date().isoformat()
        hour  = now.hour
        length = len(text.strip())

        daily: dict = self._data.setdefault("daily", {})
        day_entry: dict = daily.setdefault(
            today, {"hours": [], "message_lengths": [], "count": 0}
        )
        day_entry["hours"].append(hour)
        day_entry["message_lengths"].append(length)
        day_entry["count"] += 1

        # Mantieni solo ultimi 30 giorni
        if len(daily) > 30:
            oldest = sorted(daily.keys())[0]
            del daily[oldest]

        self._msg_count_since_summary += 1
        if self._msg_count_since_summary >= 10:
            self._update_summary()
            self._msg_count_since_summary = 0

        self._save()

    # ──────────────────────────────────────────────────────────────────────
    #  Riepilogo
    # ──────────────────────────────────────────────────────────────────────

    def _update_summary(self) -> None:
        """Ricalcola il riepilogo aggregato dai dati giornalieri."""
        daily = self._data.get("daily", {})
        if not daily:
            return

        all_lengths: list[int] = []
        hour_counts: dict[int, int] = {}

        for day_data in daily.values():
            all_lengths.extend(day_data.get("message_lengths", []))
            for h in day_data.get("hours", []):
                hour_counts[h] = hour_counts.get(h, 0) + 1

        avg_length       = int(sum(all_lengths) / len(all_lengths)) if all_lengths else 0
        most_active_hour = max(hour_counts, key=hour_counts.get) if hour_counts else None
        active_days      = len(daily)

        self._data["summary"] = {
            "avg_message_length": avg_length,
            "most_active_hour":   most_active_hour,
            "active_days":        active_days,
            "updated_at":         datetime.now().isoformat(),
        }

    # ──────────────────────────────────────────────────────────────────────
    #  Query
    # ──────────────────────────────────────────────────────────────────────

    def get_active_hours(self) -> list[int]:
        """Ore con ≥1 messaggio nelle ultime 3 giornate con dati."""
        daily = self._data.get("daily", {})
        if not daily:
            return []
        recent_days = sorted(daily.keys())[-3:]
        active: set[int] = set()
        for d in recent_days:
            for h in daily[d].get("hours", []):
                active.add(h)
        return sorted(active)

    def get_never_active_hours(self) -> list[int]:
        """Ore mai attive nell'intera storia (≥3 giorni di dati).
        Ritorna [] se ci sono meno di 3 giorni di dati — comportamento sicuro per nuove installazioni."""
        daily = self._data.get("daily", {})
        if len(daily) < 3:
            return []
        ever_active: set[int] = set()
        for day_data in daily.values():
            for h in day_data.get("hours", []):
                ever_active.add(h)
        # Tutte le 24 ore meno quelle in cui Simone ha scritto almeno una volta
        return sorted(h for h in range(24) if h not in ever_active)

    def get_summary(self) -> str:
        """Riepilogo leggibile del profilo di attività."""
        summary = self._data.get("summary", {})
        if not summary:
            return ""
        parts = []
        if summary.get("most_active_hour") is not None:
            parts.append(f"ora più attiva: {summary['most_active_hour']}:00")
        if summary.get("avg_message_length"):
            parts.append(f"lunghezza media messaggi: {summary['avg_message_length']} char")
        if summary.get("active_days"):
            parts.append(f"giorni con dati: {summary['active_days']}")
        return ", ".join(parts) if parts else ""

    def get_predictions(self, lookahead_hours: int = 3) -> list[dict]:
        """Prevede le ore più probabili di attività nelle prossime `lookahead_hours` ore.

        Analizza i dati giornalieri accumulati e restituisce le ore con frequenza
        significativa (≥3 occorrenze) nel range richiesto.
        """
        daily = self._data.get("daily", {})
        if len(daily) < 3:
            return []

        now_hour = datetime.now().hour
        target_hours = [(now_hour + i) % 24 for i in range(1, lookahead_hours + 1)]

        # Conta frequenza per ora nell'intera storia
        hour_counts: dict[int, int] = {}
        for day_data in daily.values():
            for h in day_data.get("hours", []):
                hour_counts[h] = hour_counts.get(h, 0) + 1

        predictions = []
        for h in target_hours:
            freq = hour_counts.get(h, 0)
            if freq >= 3:
                predictions.append({
                    "hour": h,
                    "topic": "attività",
                    "frequency": freq,
                })

        return sorted(predictions, key=lambda p: p["frequency"], reverse=True)
