"""
modules/admin_lockout.py – Lockout persistente per tentativi admin falliti

Stato su disco: data/lockouts.json (write_json_atomic, permissions 0o600).
Cache in-memory con flush dopo ogni cambio di stato.
Audit log: logs/admin_audit.log (JSONL, RotatingFileHandler 5 MB × 10).

Parametri:
  MAX_FAILED_ATTEMPTS = 5
  LOCKOUT_DURATION_MINUTES = 30
"""

from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from config import Config
from modules.utils import write_json_atomic

# ── Parametri ────────────────────────────────────────────────────────
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION_MINUTES = 30

# ── File ─────────────────────────────────────────────────────────────
_LOCKOUTS_FILE = Config.DATA_DIR / "lockouts.json"
_LOGS_DIR = Path("logs")
_AUDIT_LOG = _LOGS_DIR / "admin_audit.log"


# ── Audit logger (RotatingFileHandler, coerente con gli altri) ───────

def _setup_audit_logger() -> logging.Logger:
    """Crea (o recupera) il logger per admin_audit.log.
    RotatingFileHandler: 5 MB × 10 file = max 50 MB.
    """
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("cipher.admin_audit")
    if not logger.handlers:
        handler = logging.handlers.RotatingFileHandler(
            str(_AUDIT_LOG),
            maxBytes=5 * 1024 * 1024,
            backupCount=10,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger.addHandler(handler)
    return logger


class AdminLockout:
    """Lockout persistente per tentativi admin falliti.

    Singleton leggero: ogni istanza legge/scrive lo stesso file.
    Thread-safe: usa write_json_atomic (tmp + rename).
    """

    def __init__(self) -> None:
        self._state: dict = self._load()
        self._audit = _setup_audit_logger()

    # ── Query ────────────────────────────────────────────────────────

    def is_locked(self, key: str) -> tuple[bool, int]:
        """Ritorna (locked, remaining_minutes).

        Se il lockout è scaduto, lo pulisce e ritorna (False, 0).
        """
        entry = self._state.get(key)
        if entry is None:
            return False, 0

        locked_until = entry.get("locked_until")
        if not locked_until:
            return False, 0

        try:
            until = datetime.fromisoformat(locked_until)
        except (ValueError, TypeError):
            return False, 0

        if datetime.now() >= until:
            # Lockout scaduto — pulisci
            entry.pop("locked_until", None)
            entry["failed_attempts"] = 0
            self._save()
            self._log_event("lockout_expired", key, "timer scaduto")
            return False, 0

        remaining = int((until - datetime.now()).total_seconds() / 60) + 1
        return True, remaining

    # ── Mutazioni ────────────────────────────────────────────────────

    def record_failure(self, key: str, detail: str = "") -> tuple[bool, str]:
        """Incrementa contatore fallimenti. Se raggiunge MAX → lockout.

        Ritorna (is_now_locked, message_for_user).
        """
        entry = self._state.setdefault(key, {"failed_attempts": 0})
        entry["failed_attempts"] = entry.get("failed_attempts", 0) + 1
        entry["last_attempt"] = datetime.now().isoformat()
        attempts = entry["failed_attempts"]

        self._log_event(
            "failed_attempt", key,
            f"attempt {attempts}/{MAX_FAILED_ATTEMPTS} — {detail}",
        )

        if attempts >= MAX_FAILED_ATTEMPTS:
            until = datetime.now() + timedelta(minutes=LOCKOUT_DURATION_MINUTES)
            entry["locked_until"] = until.isoformat()
            entry["failed_attempts"] = 0  # reset dopo lockout
            self._save()
            self._log_event(
                "lockout_activated", key,
                f"{MAX_FAILED_ATTEMPTS} failed attempts — locked {LOCKOUT_DURATION_MINUTES} min",
            )
            return True, (
                f"Troppi tentativi falliti. "
                f"Accesso bloccato per {LOCKOUT_DURATION_MINUTES} minuti."
            )

        self._save()
        return False, "Non ti riconosco."

    def record_success(self, key: str) -> None:
        """Azzera contatore per la key dopo login riuscito."""
        if key in self._state:
            self._state[key] = {"failed_attempts": 0}
            self._save()
        self._log_event("login_success", key, "counter reset")

    # ── Persistenza ──────────────────────────────────────────────────

    def _load(self) -> dict:
        if not _LOCKOUTS_FILE.exists():
            return {}
        try:
            data = json.loads(_LOCKOUTS_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save(self) -> None:
        write_json_atomic(_LOCKOUTS_FILE, self._state, permissions=0o600)

    # ── Audit ────────────────────────────────────────────────────────

    def _log_event(self, event: str, key: str, detail: str) -> None:
        record = json.dumps({
            "timestamp": datetime.now().isoformat(),
            "event": event,
            "key": key,
            "detail": detail,
        }, ensure_ascii=False)
        self._audit.info(record)
