"""
modules/script_registry.py – Registro degli script che Cipher può eseguire

Uno script è eseguibile solo se è presente nel registro E approvato da Simone.
Ci sono due modi per aggiungere uno script:
  1. Simone lo aggiunge manualmente al file allowed_scripts.json
  2. Cipher lo scrive con write_file e poi chiede approvazione via Telegram

Struttura allowed_scripts.json:
{
  "scripts": {
    "analisi.py": {
      "approved": true,
      "added_by": "simone" | "cipher",
      "added_at": "2026-03-29 14:00",
      "description": "..."
    }
  }
}
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from config import Config

log = logging.getLogger("cipher.script_registry")

SCRIPTS_ROOT    = Path(__file__).resolve().parent.parent / "home"
REGISTRY_FILE   = SCRIPTS_ROOT / "allowed_scripts.json"


class ScriptRegistry:
    def __init__(self) -> None:
        SCRIPTS_ROOT.mkdir(parents=True, exist_ok=True)
        self._registry: dict = self._load()

    # ── Persistenza ───────────────────────────────────────────────────

    def _load(self) -> dict:
        if REGISTRY_FILE.exists():
            try:
                return json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"scripts": {}}

    def _save(self) -> None:
        REGISTRY_FILE.write_text(
            json.dumps(self._registry, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ── API pubblica ──────────────────────────────────────────────────

    def is_allowed(self, script_name: str) -> bool:
        """True solo se lo script esiste nel registro ED è approvato."""
        entry = self._registry["scripts"].get(script_name)
        if not entry:
            return False
        return entry.get("approved", False)

    def is_pending(self, script_name: str) -> bool:
        """True se lo script è nel registro ma non ancora approvato."""
        entry = self._registry["scripts"].get(script_name)
        if not entry:
            return False
        return not entry.get("approved", False)

    def register_by_cipher(self, script_name: str, description: str = "") -> None:
        """
        Cipher registra uno script appena scritto — parte come non approvato.
        Simone deve approvarlo prima che possa essere eseguito.
        """
        self._registry["scripts"][script_name] = {
            "approved":    False,
            "added_by":    "cipher",
            "added_at":    datetime.now().strftime("%Y-%m-%d %H:%M"),
            "description": description,
        }
        self._save()
        log.info("Script registrato (in attesa approvazione): %s", script_name)

    def approve(self, script_name: str) -> bool:
        """Simone approva uno script. Ritorna False se non esiste nel registro."""
        if script_name not in self._registry["scripts"]:
            return False
        self._registry["scripts"][script_name]["approved"]    = True
        self._registry["scripts"][script_name]["approved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._save()
        log.info("Script approvato: %s", script_name)
        return True

    def revoke(self, script_name: str) -> bool:
        """Revoca l'approvazione di uno script."""
        if script_name not in self._registry["scripts"]:
            return False
        self._registry["scripts"][script_name]["approved"] = False
        self._save()
        log.info("Script revocato: %s", script_name)
        return True

    def remove(self, script_name: str) -> bool:
        """Rimuove completamente uno script dal registro."""
        if script_name not in self._registry["scripts"]:
            return False
        del self._registry["scripts"][script_name]
        self._save()
        log.info("Script rimosso dal registro: %s", script_name)
        return True

    def list_all(self) -> list[dict]:
        """Lista tutti gli script nel registro con il loro stato."""
        result = []
        for name, entry in self._registry["scripts"].items():
            result.append({"name": name, **entry})
        return result

    def list_pending(self) -> list[dict]:
        """Lista script in attesa di approvazione."""
        return [{"name": n, **e} for n, e in self._registry["scripts"].items()
                if not e.get("approved", False)]
