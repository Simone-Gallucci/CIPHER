"""
modules/action_log.py – Audit log delle azioni eseguite da Cipher

Ogni azione eseguita (web search, calendar, filesystem, ecc.) viene registrata
in memory/action_log.json con timestamp, tipo, parametri e risultato.

Pulizia automatica: mantiene solo gli ultimi 30 giorni.
Non usa lock — le scritture sono atomiche via write_json_atomic.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Optional

from config import Config
from modules.auth import get_user_memory_dir, get_system_owner_id
from modules.utils import write_json_atomic

_LOG_FILE  = get_user_memory_dir(get_system_owner_id()) / "action_log.json"
_KEEP_DAYS = 30
# Pulizia ogni N log per non farlo ad ogni append
_CLEANUP_EVERY = 50


class ActionLog:
    """Audit log append-only delle azioni di Cipher.

    Singleton leggero: ogni istanza legge/scrive lo stesso file JSON.
    Thread-safe: usa write_json_atomic (tmp + rename).
    """

    def __init__(self) -> None:
        self._append_count = 0

    # ── Core ──────────────────────────────────────────────────────────────

    def log(
        self,
        action_type: str,
        params: dict,
        result: str,
        source: str = "user_request",
    ) -> None:
        """Registra un'azione eseguita.

        Args:
            action_type: nome dell'azione (es. "web_search", "calendar_create")
            params:      parametri passati all'azione (può contenere dati sensibili — troncati)
            result:      stringa di risultato (troncata a 200 char)
            source:      "user_request" | "autonomous" | "scheduled"
        """
        entry: dict = {
            "timestamp": datetime.now().isoformat(),
            "action":    action_type,
            "params":    self._sanitize_params(params),
            "result":    result[:200] if result else "",
            "source":    source,
        }
        try:
            entries = self._load()
            entries.append(entry)
            write_json_atomic(_LOG_FILE, entries, permissions=0o600)
        except Exception:
            pass  # Il log non deve mai bloccare l'esecuzione

        self._append_count += 1
        if self._append_count % _CLEANUP_EVERY == 0:
            self._cleanup_old()

    # ── Query ─────────────────────────────────────────────────────────────

    def get_today(self) -> list[dict]:
        """Ritorna le azioni registrate oggi."""
        today = datetime.now().date().isoformat()
        return [e for e in self._load() if e.get("timestamp", "").startswith(today)]

    def get_summary(self, days: int = 1) -> str:
        """Ritorna un sommario leggibile delle azioni degli ultimi N giorni.

        Esempio output:
            Oggi: 3× web_search, 2× calendar_list, 1× calendar_create
        """
        cutoff = datetime.now() - timedelta(days=days)
        entries = [
            e for e in self._load()
            if _parse_ts(e.get("timestamp", "")) >= cutoff
        ]
        if not entries:
            return ""

        # Conta per tipo
        counts: dict[str, int] = {}
        for e in entries:
            action = e.get("action", "sconosciuta")
            counts[action] = counts.get(action, 0) + 1

        parts = [f"{n}× {a}" for a, n in sorted(counts.items(), key=lambda x: -x[1])]
        label = "Oggi" if days == 1 else f"Ultimi {days} giorni"
        return f"{label}: {', '.join(parts)}"

    def get_entries(self, days: int = 1) -> list[dict]:
        """Ritorna le entry grezze degli ultimi N giorni."""
        cutoff = datetime.now() - timedelta(days=days)
        return [
            e for e in self._load()
            if _parse_ts(e.get("timestamp", "")) >= cutoff
        ]

    # ── Persistenza ───────────────────────────────────────────────────────

    def _load(self) -> list[dict]:
        if not _LOG_FILE.exists():
            return []
        try:
            data = json.loads(_LOG_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _cleanup_old(self) -> None:
        """Rimuove entry più vecchie di KEEP_DAYS giorni."""
        cutoff = datetime.now() - timedelta(days=_KEEP_DAYS)
        entries = self._load()
        fresh = [e for e in entries if _parse_ts(e.get("timestamp", "")) >= cutoff]
        if len(fresh) < len(entries):
            try:
                write_json_atomic(_LOG_FILE, fresh, permissions=0o600)
            except Exception:
                pass

    # ── Utility ───────────────────────────────────────────────────────────

    @staticmethod
    def _sanitize_params(params: dict) -> dict:
        """Ritorna i parametri puliti — rimuove campi sensibili, tronca valori lunghi."""
        _SKIP = {"content", "body", "text", "image_b64"}
        result = {}
        for k, v in params.items():
            if k in _SKIP:
                result[k] = "[...]"
            elif isinstance(v, str) and len(v) > 100:
                result[k] = v[:100] + "..."
            else:
                result[k] = v
        return result


# ── Helpers ───────────────────────────────────────────────────────────────

def _parse_ts(ts: str) -> datetime:
    """Parsa un timestamp ISO — ritorna epoch se non valido."""
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return datetime.min
