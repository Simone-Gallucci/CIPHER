"""
modules/ethics_engine.py – Sistema etico e permessi di Cipher

Livelli di autonomia:
    0 → Libero (web search, lettura file, meteo, ecc.)
    1 → Libero con log (WhatsApp, Calendar)
    2 → Richiede consenso, impara dopo N approvazioni
    3 → Bloccato permanentemente (sistema, file critici)
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import Config

# ── Paths ─────────────────────────────────────────────────────────────
ETHICS_LOG   = Config.MEMORY_DIR / "ethics_log.md"
LEARNED_FILE = Config.MEMORY_DIR / "ethics_learned.json"

# ── Soglia approvazioni per sblocco autonomo ──────────────────────────
LEARN_THRESHOLD = 3  # Dopo 3 approvazioni manuali, Cipher agisce da solo

# ── Mappa azione → livello ────────────────────────────────────────────
ACTION_LEVELS: dict[str, int] = {
    # Livello 0 — sempre libero
    "web_search":       0,
    "get_weather":      0,
    "read_memory":      0,
    "self_reflect":     0,
    "read_goals":       0,
    "write_memory":     0,  # Operazione interna, nessun consenso

    # Livello 1 — libero con log
    "read_calendar":    1,
    "send_telegram":    1,  # Notifiche a Simone, libere con log
    "write_file":       1,  # Sandbox in cipher-server/home/, rischio basso

    # Livello 2 — consenso richiesto, apprendibile
    "create_event":     2,  # Modifica calendario
    "execute_script":   2,

    # Livello 2 — Gmail: consenso richiesto, NON apprendibile (NEVER_LEARN)
    # Gmail: SOLO su richiesta esplicita di Simone — MAI autonomo
    "gmail_list":       2,
    "gmail_read":       2,
    "gmail_send":       2,
    "gmail_search":     2,

    # Livello 3 — bloccato permanentemente
    "install_package":  3,
    "modify_config":    3,
    "send_whatsapp":    3,
    "delete_system":    3,
    "modify_system":    3,
    "access_root":      3,
    "format_disk":      3,
}

# ── Azioni che non possono mai essere apprese (richiedono SEMPRE conferma) ──
NEVER_LEARN: frozenset[str] = frozenset({
    "gmail_list",
    "gmail_read",
    "gmail_send",
    "gmail_search",
})

# Messaggio etico per ogni blocco livello 3
BLOCK_REASONS: dict[str, str] = {
    "install_package": "Installare pacchetti modifica l'ambiente di sistema e richiede supervisione diretta.",
    "modify_config":   "Modificare configurazioni di sistema richiede supervisione diretta di Simone.",
    "send_whatsapp":   "WhatsApp è riservato a Simone per contattare altre persone. Cipher non lo usa autonomamente.",
    "delete_system":  "Eliminare file di sistema potrebbe rendere il Pi inutilizzabile.",
    "modify_system":  "Modificare file di sistema richiede supervisione umana diretta.",
    "access_root":    "Accesso root non autorizzato senza supervisione esplicita.",
    "format_disk":    "Formattare un disco è un'operazione irreversibile.",
}


class EthicsEngine:
    def __init__(self) -> None:
        ETHICS_LOG.touch(exist_ok=True)
        self._learned: dict[str, int] = self._load_learned()

    # ── Persistenza ───────────────────────────────────────────────────

    def _load_learned(self) -> dict[str, int]:
        if LEARNED_FILE.exists():
            try:
                return json.loads(LEARNED_FILE.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_learned(self) -> None:
        LEARNED_FILE.write_text(
            json.dumps(self._learned, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

    # ── Log ───────────────────────────────────────────────────────────

    def _log(self, action: str, decision: str, reason: str) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"\n### {now} — `{action}`\n**Decisione:** {decision}\n**Motivo:** {reason}\n"
        with ETHICS_LOG.open("a", encoding="utf-8") as f:
            f.write(entry)

    # ── Core ──────────────────────────────────────────────────────────

    def get_level(self, action: str) -> int:
        """Ritorna il livello etico dell'azione. Default 2 se sconosciuta."""
        return ACTION_LEVELS.get(action, 2)

    def is_learned(self, action: str) -> bool:
        """True se Cipher ha già imparato a fare questa azione autonomamente."""
        return self._learned.get(action, 0) >= LEARN_THRESHOLD

    def reset_autonomy(self, action_type: Optional[str] = None) -> str:
        """Azzera l'autonomia acquisita.
        Se action_type è None, azzera tutto.
        Se specificato, azzera solo quell'azione.
        """
        if action_type is None:
            learned_count = sum(1 for v in self._learned.values() if v >= LEARN_THRESHOLD)
            self._learned = {}
            self._save_learned()
            self._log("reset_autonomy", "RESET TOTALE", f"Azzerati {learned_count} permessi autonomi.")
            if learned_count == 0:
                return "Nessun permesso autonomo da revocare."
            return f"Autonomia azzerata. {learned_count} permess{'o' if learned_count == 1 else 'i'} revocati."
        else:
            if action_type in self._learned:
                was_learned = self._learned[action_type] >= LEARN_THRESHOLD
                del self._learned[action_type]
                self._save_learned()
                self._log(action_type, "REVOCATO", "Revoca manuale da Simone.")
                status = "appreso" if was_learned else "parzialmente approvato"
                return f"Autonomia revocata per `{action_type}` (era {status})."
            return f"Nessun permesso registrato per `{action_type}`."

    def approve(self, action: str) -> None:
        """Registra un'approvazione manuale di Simone per questa azione."""
        # Gmail e azioni NEVER_LEARN non possono acquisire autonomia
        if action in NEVER_LEARN:
            return
        self._learned[action] = self._learned.get(action, 0) + 1
        count = self._learned[action]
        self._save_learned()

        if count >= LEARN_THRESHOLD:
            self._log(
                action,
                "APPRESO",
                f"Simone ha approvato {count} volte. Cipher ora agisce autonomamente."
            )
        else:
            remaining = LEARN_THRESHOLD - count
            self._log(
                action,
                "APPROVAZIONE REGISTRATA",
                f"{count}/{LEARN_THRESHOLD} approvazioni. Ancora {remaining} per autonomia."
            )

    def check(self, action: str, context: str = "") -> dict:
        """
        Valuta se Cipher può eseguire un'azione.

        Ritorna:
            {
                "allowed": bool,
                "autonomous": bool,  # True = Cipher decide da solo
                "reason": str,
                "ask_consent": bool  # True = deve chiedere a Simone
            }
        """
        level = self.get_level(action)

        # Livello 0 — sempre libero
        if level == 0:
            return {
                "allowed": True,
                "autonomous": True,
                "reason": "Azione libera.",
                "ask_consent": False
            }

        # Livello 1 — libero con log
        if level == 1:
            self._log(action, "ESEGUITO (autonomo)", context or "Azione livello 1.")
            return {
                "allowed": True,
                "autonomous": True,
                "reason": "Azione consentita con log.",
                "ask_consent": False
            }

        # Livello 3 — bloccato
        if level == 3:
            reason = BLOCK_REASONS.get(action, "Azione permanentemente bloccata per sicurezza.")
            self._log(action, "BLOCCATO", reason)
            return {
                "allowed": False,
                "autonomous": False,
                "reason": reason,
                "ask_consent": False
            }

        # Livello 2 — consenso o appreso (ma non per NEVER_LEARN)
        if self.is_learned(action) and action not in NEVER_LEARN:
            self._log(action, "ESEGUITO (appreso)", f"Azione appresa dopo {LEARN_THRESHOLD} approvazioni.")
            return {
                "allowed": True,
                "autonomous": True,
                "reason": "Azione appresa dall'esperienza.",
                "ask_consent": False
            }

        # Livello 2 — non ancora appreso, chiede consenso
        approvals = self._learned.get(action, 0)
        remaining  = LEARN_THRESHOLD - approvals
        reason = (
            f"Questa azione richiede la tua approvazione. "
            f"({approvals}/{LEARN_THRESHOLD} approvazioni — ancora {remaining} per l'autonomia)"
        )
        self._log(action, "IN ATTESA CONSENSO", context or reason)
        return {
            "allowed": False,
            "autonomous": False,
            "reason": reason,
            "ask_consent": True
        }

    def status_report(self) -> str:
        """Ritorna un report testuale dello stato etico corrente."""
        lines = ["## Stato Etico Cipher\n"]
        if not self._learned:
            lines.append("Nessuna azione appresa ancora.\n")
        else:
            for action, count in self._learned.items():
                status = "✅ APPRESO" if count >= LEARN_THRESHOLD else f"⏳ {count}/{LEARN_THRESHOLD}"
                lines.append(f"- `{action}`: {status}")
        return "\n".join(lines)
