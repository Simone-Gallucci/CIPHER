"""
modules/contacts.py – Rubrica contatti di Cipher

Salva in memory/contacts.json una rubrica di nomi → numeri/ID.
Cipher risolve "mamma" → numero WhatsApp senza che Simone debba specificarlo.

Schema per entry:
    {
        "alias_chiave": {
            "nome":        str,
            "whatsapp":    str | null,   # es. "393317704542"
            "telegram_id": int | null,
            "aliases":     [str, ...]    # altri nomi/abbreviazioni accettati
        }
    }

Il matching è case-insensitive e controlla sia la chiave che tutti gli aliases.
"""

import json
import logging
from typing import Optional

from config import Config
from modules.utils import write_json_atomic

log = logging.getLogger("cipher.contacts")

CONTACTS_FILE = Config.MEMORY_DIR / "contacts.json"


def _load() -> dict:
    if not CONTACTS_FILE.exists():
        return {}
    try:
        return json.loads(CONTACTS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.error("Errore lettura contacts.json: %s", e)
        return {}


def _save(contacts: dict) -> None:
    write_json_atomic(CONTACTS_FILE, contacts)


def _normalize(name: str) -> str:
    return name.strip().lower()


def resolve(name: str) -> Optional[dict]:
    """Risolve un nome/alias nel contatto corrispondente. Restituisce l'entry o None."""
    needle = _normalize(name)
    contacts = _load()
    for key, entry in contacts.items():
        if _normalize(key) == needle:
            return entry
        for alias in entry.get("aliases", []):
            if _normalize(alias) == needle:
                return entry
    return None


def add(alias: str, nome: str, whatsapp: Optional[str] = None,
        telegram_id: Optional[int] = None, aliases: Optional[list] = None) -> str:
    contacts = _load()
    key = _normalize(alias)
    contacts[key] = {
        "nome":        nome,
        "whatsapp":    whatsapp,
        "telegram_id": telegram_id,
        "aliases":     [_normalize(a) for a in (aliases or [])],
    }
    _save(contacts)
    return f"Contatto '{nome}' salvato."


def remove(alias: str) -> str:
    contacts = _load()
    key = _normalize(alias)
    # Cerca per chiave principale
    if key in contacts:
        nome = contacts[key].get("nome", alias)
        del contacts[key]
        _save(contacts)
        return f"Contatto '{nome}' rimosso."
    # Cerca tra gli aliases
    for k, entry in contacts.items():
        if key in [_normalize(a) for a in entry.get("aliases", [])]:
            nome = entry.get("nome", k)
            del contacts[k]
            _save(contacts)
            return f"Contatto '{nome}' rimosso."
    return f"Contatto '{alias}' non trovato."


def update(alias: str, **fields) -> str:
    contacts = _load()
    key = _normalize(alias)
    # Cerca per chiave principale
    if key not in contacts:
        # Cerca tra gli aliases
        for k, entry in contacts.items():
            if key in [_normalize(a) for a in entry.get("aliases", [])]:
                key = k
                break
        else:
            return f"Contatto '{alias}' non trovato."
    for field, value in fields.items():
        if field in ("nome", "whatsapp", "telegram_id", "aliases"):
            contacts[key][field] = value
    _save(contacts)
    return f"Contatto '{contacts[key]['nome']}' aggiornato."


def list_all() -> str:
    contacts = _load()
    if not contacts:
        return "Nessun contatto salvato."
    lines = []
    for key, entry in contacts.items():
        parts = [entry.get("nome", key)]
        if entry.get("whatsapp"):
            parts.append(f"WA: {entry['whatsapp']}")
        if entry.get("telegram_id"):
            parts.append(f"TG: {entry['telegram_id']}")
        if entry.get("aliases"):
            parts.append(f"alias: {', '.join(entry['aliases'])}")
        lines.append(" | ".join(parts))
    return "\n".join(lines)
