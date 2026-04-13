"""
modules/llm_usage.py – Tracking chiamate LLM per giornata

Contatore leggero: registra quante chiamate per modello/tipo al giorno.
Dati in memory/llm_usage.json. Mantiene ultimi 30 giorni.
"""

import json
import logging
from datetime import date
from pathlib import Path
from threading import Lock

from config import Config
from modules.utils import write_json_atomic

log = logging.getLogger("cipher.llm_usage")

USAGE_FILE: Path = Config.MEMORY_DIR / "llm_usage.json"
_MAX_DAYS = 30

_lock = Lock()
_data: dict = {}


def _load() -> dict:
    global _data
    if USAGE_FILE.exists():
        try:
            _data = json.loads(USAGE_FILE.read_text(encoding="utf-8"))
        except Exception:
            _data = {}
    return _data


def _save() -> None:
    try:
        write_json_atomic(USAGE_FILE, _data)
    except Exception as e:
        log.warning("llm_usage save error: %s", e)


def _prune() -> None:
    """Mantiene solo gli ultimi _MAX_DAYS giorni."""
    if len(_data) > _MAX_DAYS:
        oldest = sorted(_data.keys())[: len(_data) - _MAX_DAYS]
        for k in oldest:
            del _data[k]


def record(model: str, call_type: str = "default") -> None:
    """Registra una chiamata LLM. Thread-safe."""
    today = date.today().isoformat()
    with _lock:
        if not _data:
            _load()
        day = _data.setdefault(today, {})
        key = f"{model}|{call_type}"
        day[key] = day.get(key, 0) + 1
        _prune()
        _save()


def get_today() -> dict:
    """Ritorna le statistiche di oggi: {model|type: count}."""
    today = date.today().isoformat()
    with _lock:
        if not _data:
            _load()
        return dict(_data.get(today, {}))


def get_summary(days: int = 7) -> dict:
    """Ritorna conteggi aggregati per gli ultimi N giorni."""
    with _lock:
        if not _data:
            _load()
        recent_keys = sorted(_data.keys())[-days:]
        totals: dict[str, int] = {}
        for day_key in recent_keys:
            for k, v in _data.get(day_key, {}).items():
                totals[k] = totals.get(k, 0) + v
        return totals
