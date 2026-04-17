"""
modules/message_rate_limiter.py – Rate limiting per-utente sui messaggi

Stato su disco: data/rate_limits.json (write_json_atomic, permissions 0o600).
Cache in-memory con flush dopo ogni cambio di stato.

Parametri:
  MAX_PER_MINUTE = 10
  MAX_PER_HOUR = 60

Cleanup TTL: record più vecchi di 1 ora rimossi al check().
"""

# TODO(performance): con molti utenti, considerare flush periodico
# (es. ogni 10 secondi) invece che a ogni messaggio. Per ora il
# carico è trascurabile con un singolo utente.

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Optional

from config import Config
from modules.utils import write_json_atomic

# ── Parametri ────────────────────────────────────────────────────────
MAX_PER_MINUTE = 10
MAX_PER_HOUR = 60

# ── File ─────────────────────────────────────────────────────────────
_RATE_FILE = Config.DATA_DIR / "rate_limits.json"


class MessageRateLimiter:
    """Rate limiting per-utente sui messaggi in arrivo.

    Controlla che un sender non superi MAX_PER_MINUTE o MAX_PER_HOUR.
    Stato persistente su disco, cache in-memory.
    """

    def __init__(self) -> None:
        self._state: dict = self._load()

    # ── Query ────────────────────────────────────────────────────────

    def check(self, sender_id: str) -> tuple[bool, str]:
        """Controlla se il sender può inviare.

        Ritorna (allowed, message).
        Se non allowed, message contiene il testo cortese.
        Fa cleanup TTL dei record >1h.
        """
        self._cleanup(sender_id)

        entry = self._state.get(sender_id)
        if entry is None:
            return True, ""

        timestamps = entry.get("timestamps", [])
        now = datetime.now()

        # Conteggio ultimo minuto
        one_min_ago = (now - timedelta(minutes=1)).isoformat()
        in_last_minute = sum(1 for ts in timestamps if ts >= one_min_ago)

        if in_last_minute >= MAX_PER_MINUTE:
            wait_seconds = 60 - int((now - datetime.fromisoformat(timestamps[-MAX_PER_MINUTE])).total_seconds())
            wait_seconds = max(wait_seconds, 1)
            return False, (
                f"Un momento — troppi messaggi in poco tempo. "
                f"Riprova tra {wait_seconds} secondi."
            )

        # Conteggio ultima ora
        one_hour_ago = (now - timedelta(hours=1)).isoformat()
        in_last_hour = sum(1 for ts in timestamps if ts >= one_hour_ago)

        if in_last_hour >= MAX_PER_HOUR:
            wait_minutes = 60 - int((now - datetime.fromisoformat(timestamps[-MAX_PER_HOUR])).total_seconds() / 60)
            wait_minutes = max(wait_minutes, 1)
            return False, (
                f"Un momento — troppi messaggi in poco tempo. "
                f"Riprova tra {wait_minutes} minuti."
            )

        return True, ""

    # ── Mutazione ────────────────────────────────────────────────────

    def record(self, sender_id: str) -> None:
        """Registra un messaggio inviato. Scrive su disco."""
        entry = self._state.setdefault(sender_id, {"timestamps": []})
        entry["timestamps"].append(datetime.now().isoformat())
        self._save()

    # ── Cleanup ──────────────────────────────────────────────────────

    def _cleanup(self, sender_id: str) -> None:
        """Rimuove timestamp più vecchi di 1 ora per il sender."""
        entry = self._state.get(sender_id)
        if entry is None:
            return

        one_hour_ago = (datetime.now() - timedelta(hours=1)).isoformat()
        old_ts = entry.get("timestamps", [])
        fresh = [ts for ts in old_ts if ts >= one_hour_ago]

        if len(fresh) < len(old_ts):
            entry["timestamps"] = fresh
            if not fresh:
                del self._state[sender_id]
            self._save()

    # ── Persistenza ──────────────────────────────────────────────────

    def _load(self) -> dict:
        if not _RATE_FILE.exists():
            return {}
        try:
            data = json.loads(_RATE_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save(self) -> None:
        write_json_atomic(_RATE_FILE, self._state, permissions=0o600)
