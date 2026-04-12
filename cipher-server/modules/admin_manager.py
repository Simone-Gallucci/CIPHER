"""
modules/admin_manager.py – Gestione del legame permanente Cipher↔Admin

admin.json è l'unico file del sistema che non viene mai toccato da reset,
tabula rasa, o qualsiasi pulizia automatica. Contiene l'identità verificata
dell'admin e la parola segreta hashata che permette il riconoscimento post-reset.
"""

import hashlib
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import Config

log = logging.getLogger("cipher.admin_manager")

ADMIN_FILE:     Path = Config.BASE_DIR / "data" / "admin.json"
CHANGELOG_FILE: Path = Config.BASE_DIR / "data" / "changelog.json"

EMPTY_ADMIN: dict = {
    "identity": {
        "name": "",
        "age": None,
        "location": "",
        "occupation": "",
    },
    "relationship": {
        "confidence_score": 0.0,
        "first_message_date": "",
        "bond_date": "",
        "password_hash": "",
        "password_salt": "",
    },
    "memories": {
        "episodes": [],
        "interests": [],
        "recurring_topics": [],
    },
    "emotional_state": {
        "emotional_log_last10": [],
        "relationship_tone": "",
    },
    "important_moments": [],
    # Dati comportamentali salvati al momento del legame e aggiornati periodicamente
    "patterns": {
        "daily": {},
        "summary": {},
    },
    "checksum": "",
}


def hash_password(password: str) -> tuple[str, str]:
    """Genera hash SHA-256 con salt casuale. Ritorna (hashed, salt)."""
    salt = os.urandom(32).hex()
    hashed = hashlib.sha256((password + salt).encode()).hexdigest()
    return hashed, salt


def verify_password(password: str, hashed: str, salt: str) -> bool:
    """Verifica password contro hash+salt salvati."""
    return hashlib.sha256((password + salt).encode()).hexdigest() == hashed


def compute_checksum(data: dict) -> str:
    """SHA-256 del JSON serializzato senza il campo checksum."""
    d = {k: v for k, v in data.items() if k != "checksum"}
    return hashlib.sha256(
        json.dumps(d, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def save_admin(data: dict) -> None:
    """Aggiunge il checksum e salva su data/admin.json in modo atomico."""
    ADMIN_FILE.parent.mkdir(parents=True, exist_ok=True)
    d = dict(data)
    d["checksum"] = compute_checksum(d)
    # Scrittura atomica: scrivi su tmp poi rinomina
    tmp = ADMIN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False))
    tmp.rename(ADMIN_FILE)


def load_admin() -> Optional[dict]:
    """
    Carica admin.json, verifica il checksum.
    Ritorna None se il file non esiste o se il checksum non corrisponde (corrotto).
    """
    if not ADMIN_FILE.exists():
        return None
    try:
        data = json.loads(ADMIN_FILE.read_text(encoding="utf-8"))
        expected = compute_checksum(data)
        if data.get("checksum") != expected:
            return None  # corrotto
        return data
    except Exception:
        return None


def admin_exists() -> bool:
    """True se admin.json esiste ed è integro."""
    return load_admin() is not None


def log_backup(original_path: "str | Path", backup_path: "str | Path") -> None:
    """Registra un backup in data/changelog.json.
    Scrittura atomica — sicura con processi multipli.
    Descrizione fissa: niente LLM per tenere il percorso critico veloce."""
    try:
        CHANGELOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        # Leggi l'esistente (o parti da lista vuota)
        existing: list = []
        if CHANGELOG_FILE.exists():
            try:
                existing = json.loads(CHANGELOG_FILE.read_text(encoding="utf-8"))
                if not isinstance(existing, list):
                    existing = []
            except Exception:
                existing = []

        existing.append({
            "timestamp":   datetime.now().isoformat(),
            "original":    str(original_path),
            "backup":      str(backup_path),
            "description": "Backup automatico prima della modifica.",
        })

        # Mantieni ultimi 200 backup
        existing = existing[-200:]

        tmp = CHANGELOG_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.rename(CHANGELOG_FILE)
    except Exception as e:
        log.warning("log_backup error: %s", e)
