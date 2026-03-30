"""
modules/impact_tracker.py – Meta-cognizione sull'efficacia delle azioni di Cipher

Traccia ogni azione proattiva (messaggi, news, obiettivi completati) e registra
la risposta di Simone. Cipher usa questi dati per capire cosa funziona e cosa no,
e per adattare il proprio comportamento nel tempo.
"""

import json
from datetime import datetime
from typing import Optional

from config import Config


# Tipi di azione tracciabili
ACTION_TYPES = {
    "proactive_message": "Messaggio inviato spontaneamente da Cipher",
    "checkin":           "Check-in dopo inattività",
    "news_shared":       "Notizia condivisa su argomento di interesse",
    "goal_result":       "Risultato di un obiettivo autonomo notificato",
    "reminder":          "Promemoria o avviso evento",
    "night_summary":     "Sommario notturno inviato",
}

MAX_ENTRIES = 300


class ImpactTracker:
    def __init__(self):
        self._file = Config.MEMORY_DIR / "impact_log.json"
        self._log: list[dict] = self._load()
        self._pending_id: Optional[int] = None  # ultima azione proattiva, in attesa di valutazione

    # ── Persistenza ───────────────────────────────────────────────────

    def _load(self) -> list:
        if self._file.exists():
            try:
                return json.loads(self._file.read_text(encoding="utf-8"))
            except Exception:
                return []
        return []

    def _save(self):
        self._file.write_text(
            json.dumps(self._log, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── API pubblica ──────────────────────────────────────────────────

    def log_action(
        self,
        action_type: str,
        content: str,
        context: str = "",
    ) -> int:
        """
        Registra un'azione proattiva. Ritorna l'ID dell'entry.

        Args:
            action_type: Uno dei tipi in ACTION_TYPES.
            content:     Testo dell'azione (es. il messaggio inviato).
            context:     Contesto opzionale (es. stato emotivo, motivo).
        """
        entry = {
            "id": len(self._log) + 1,
            "timestamp": datetime.now().isoformat(),
            "action_type": action_type,
            "content": content[:500],
            "context": context[:200],
            "response": None,   # Risposta di Simone
            "impact": None,     # "positive" | "negative" | "neutral"
        }
        self._log.append(entry)
        if len(self._log) > MAX_ENTRIES:
            self._log = self._log[-MAX_ENTRIES:]
        self._save()
        self._pending_id = entry["id"]
        return entry["id"]

    def evaluate_response(self, user_message: str, brain=None) -> None:
        """
        Valuta automaticamente l'impatto dell'ultima azione proattiva
        basandosi sulla risposta successiva di Simone.
        Da chiamare in Brain.think() quando arriva un messaggio dopo un'azione loggata.
        """
        if self._pending_id is None:
            return

        entry = next((e for e in self._log if e["id"] == self._pending_id), None)
        if not entry or entry["impact"] is not None:
            self._pending_id = None
            return

        impact = "neutral"
        if brain:
            try:
                result = brain._call_llm_silent(
                    f"Cipher ha inviato questo messaggio a Simone:\n\"{entry['content']}\"\n\n"
                    f"Simone ha risposto:\n\"{user_message[:300]}\"\n\n"
                    f"L'impatto del messaggio di Cipher è stato positivo, negativo o neutro? "
                    f"Rispondi con una sola parola: positivo, negativo, o neutro."
                )
                r = result.strip().lower()
                if "positiv" in r:
                    impact = "positive"
                elif "negativ" in r:
                    impact = "negative"
                else:
                    impact = "neutral"
            except Exception:
                pass

        entry["impact"] = impact
        entry["response"] = user_message[:200]
        self._pending_id = None
        self._save()

    def get_effectiveness_summary(self) -> str:
        """Ritorna un sommario leggibile dell'efficacia delle azioni recenti."""
        evaluated = [e for e in self._log[-100:] if e.get("impact")]
        if len(evaluated) < 3:
            return "Dati insufficienti per valutare l'efficacia."

        total    = len(evaluated)
        positive = sum(1 for e in evaluated if e["impact"] == "positive")
        negative = sum(1 for e in evaluated if e["impact"] == "negative")
        neutral  = total - positive - negative

        lines = [
            f"Efficacia azioni proattive (ultimi {total} casi valutati):",
            f"  ✅ Positivo : {positive} ({100 * positive // total}%)",
            f"  ➖ Neutro   : {neutral}  ({100 * neutral  // total}%)",
            f"  ❌ Negativo : {negative} ({100 * negative // total}%)",
        ]

        # Per tipo di azione
        by_type: dict[str, dict] = {}
        for e in evaluated:
            t = e["action_type"]
            if t not in by_type:
                by_type[t] = {"pos": 0, "neg": 0, "tot": 0}
            by_type[t]["tot"] += 1
            if e["impact"] == "positive":
                by_type[t]["pos"] += 1
            elif e["impact"] == "negative":
                by_type[t]["neg"] += 1

        for t, stats in by_type.items():
            if stats["tot"] >= 2:
                rate = 100 * stats["pos"] // stats["tot"]
                lines.append(f"  {t}: {rate}% positività ({stats['tot']} casi)")

        return "\n".join(lines)

    def get_pending_evaluation_count(self) -> int:
        return sum(1 for e in self._log[-20:] if e.get("impact") is None)

    def has_pending(self) -> bool:
        return self._pending_id is not None
