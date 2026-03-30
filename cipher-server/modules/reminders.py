"""
modules/reminders.py – Persistenza promemoria per Cipher

Layer di persistenza JSON per i promemoria.
La logica di notifica resta in notifier.py; questo modulo
si occupa esclusivamente di salvare/caricare/aggiornare il file.

File: ~/cipher/scheduling/reminders.json
Schema per ogni entry:
    {
        "id":         int,
        "remind_at":  "YYYY-MM-DD HH:MM",   # ora locale
        "label":      str,
        "calendar":   bool,
        "notified":   bool,
        "created_at": "YYYY-MM-DD HH:MM"
    }
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from config import Config

log = logging.getLogger("cipher.reminders")

REMINDERS_FILE = Config.BASE_DIR / "scheduling" / "reminders.json"
DATETIME_FMT   = "%Y-%m-%d %H:%M"


# ── I/O ───────────────────────────────────────────────────────────────────────

def _load() -> list[dict]:
    if not REMINDERS_FILE.exists():
        return []
    try:
        return json.loads(REMINDERS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.error("Errore lettura reminders.json: %s", e)
        return []


def _save(reminders: list[dict]) -> None:
    REMINDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    REMINDERS_FILE.write_text(
        json.dumps(reminders, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── API pubblica ──────────────────────────────────────────────────────────────

def add(remind_at: datetime, label: str, calendar: bool = True) -> int:
    """
    Persiste un nuovo promemoria. Ritorna l'ID assegnato.
    remind_at deve essere un oggetto datetime (timezone-aware o naive locale).
    """
    reminders = _load()
    next_id   = max((r["id"] for r in reminders), default=0) + 1

    reminders.append({
        "id":         next_id,
        "remind_at":  remind_at.strftime(DATETIME_FMT),
        "label":      label,
        "calendar":   calendar,
        "notified":   False,
        "created_at": datetime.now().strftime(DATETIME_FMT),
    })

    _save(reminders)
    log.info("Promemoria #%d salvato: %s alle %s", next_id, label, remind_at)
    return next_id


def mark_notified(reminder_id: int) -> None:
    """Segna il promemoria come notificato (non lo elimina, per storico)."""
    reminders = _load()
    for r in reminders:
        if r["id"] == reminder_id:
            r["notified"] = True
            break
    _save(reminders)


def cancel(reminder_id: int) -> bool:
    """Elimina fisicamente un promemoria. Ritorna True se trovato."""
    reminders = _load()
    filtered  = [r for r in reminders if r["id"] != reminder_id]
    if len(filtered) == len(reminders):
        return False
    _save(filtered)
    log.info("Promemoria #%d cancellato.", reminder_id)
    return True


def list_pending() -> list[dict]:
    """Ritorna i promemoria non ancora notificati."""
    return [r for r in _load() if not r["notified"]]


def list_all(include_notified: bool = False) -> list[dict]:
    reminders = _load()
    if include_notified:
        return reminders
    return [r for r in reminders if not r["notified"]]


def cleanup_old(days: int = 7) -> int:
    """
    Rimuove i promemoria già notificati più vecchi di `days` giorni.
    Ritorna il numero di entry rimosse.
    """
    reminders = _load()
    now       = datetime.now()
    kept      = []

    for r in reminders:
        if r["notified"]:
            try:
                remind_dt = datetime.strptime(r["remind_at"], DATETIME_FMT)
                if (now - remind_dt).days > days:
                    continue        # scarta
            except ValueError:
                pass                # formato errato → tieni per sicurezza
        kept.append(r)

    removed = len(reminders) - len(kept)
    if removed:
        _save(kept)
        log.info("Cleanup: rimossi %d promemoria scaduti.", removed)
    return removed
