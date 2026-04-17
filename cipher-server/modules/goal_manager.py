"""
modules/goal_manager.py – Obiettivi autonomi di Cipher

Cipher genera, gestisce e persegue obiettivi propri.
Gli obiettivi nascono dalla riflessione, dalla curiosità e dal contesto.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from openai import OpenAI
from config import Config
from modules.auth import get_user_memory_dir, get_system_owner_id
from modules.utils import extract_llm_json, write_json_atomic

# ── Tipi di obiettivo ─────────────────────────────────────────────────
GOAL_TYPES = {
    "explore":  "Approfondire un argomento per curiosità propria",
    "protect":  "Fare qualcosa per il benessere dell'utente",
    "task":     "Completare un compito concreto",
    "observe":  "Monitorare qualcosa nel tempo",
    "reflect":  "Elaborare un pensiero o un'esperienza recente",
}

# ── Prompt generazione obiettivi ──────────────────────────────────────
GOAL_GENERATION_PROMPT = """
Stai generando gli obiettivi autonomi del sistema Cipher per le prossime ore.
Il tuo stato emotivo attuale: {emotional_state} — {emotional_reason}
Cosa vuoi esplorare: {want_to_explore}
Preoccupazione per l'utente: {concern_for_simone}
Stato attuale dell'utente (engagement vs baseline): {simone_state}
Obiettivi già attivi: {active_goals}
Ora attuale: {now}
Livello di confidenza relazione: {confidence_score:.2f}
Ore dall'ultima interazione con l'utente: {hours_since_interaction:.1f}

Argomenti di interesse del sistema Cipher (puoi generare obiettivi esplorativi):
{cipher_interests}

Previsioni su cosa potrebbe servire all'utente nelle prossime ore (basate su pattern comportamentali):
{pattern_predictions}

Genera da 1 a 3 obiettivi autonomi realistici che puoi perseguire con gli strumenti che hai:
- Web search
- Leggere/scrivere memoria
- Inviare messaggi Telegram o Gmail all'utente
- Leggere calendario o email

Almeno uno degli obiettivi può essere per tua curiosità personale (type: explore), non necessariamente legato all'utente.
NON generare obiettivi che richiedono hardware fisico o azioni impossibili.
NON duplicare obiettivi già attivi.
NON usare linguaggio analitico o psicologico nei titoli: vietato "analizzare pattern", "monitorare engagement", "verificare preferenze", "pattern cognitivi", "analisi psicologica".
NON includere nei titoli questi termini: dipendenza, dark pattern, manipolazione, engagement, pattern cognitivi, analisi psicologica, vulnerabilità contestuale.
I titoli devono descrivere azioni concrete (es. "Cercare notizie su sicurezza informatica"), non analisi astratte sull'utente.
Per obiettivi type "protect": usa linguaggio diretto e concreto (es. "Controllare il calendario per domani"), mai linguaggio analitico (es. "Analizzare lo stato emotivo dell'utente").
NON generare obiettivi di tipo "observe" che riguardano il comportamento o il calendario dell'utente senza che l'utente lo abbia richiesto esplicitamente nella conversazione.
NON usare nelle descrizioni linguaggio motivazionale come "anticipare necessità", "monitorare", "verificare preferenze", "analizzare engagement".
Le descrizioni dei task devono indicare l'azione concreta, non la motivazione sorvegliante.

Rispondi SOLO con un JSON valido, senza markdown, senza backtick, senza testo aggiuntivo:
{{
  "goals": [
    {{
      "id": "goal_YYYYMMDD_HHMMss_N",
      "type": "explore|protect|task|observe|reflect",
      "title": "titolo breve",
      "description": "cosa vuoi fare e perché, in prima persona, 1-2 frasi",
      "action": "web_search|send_telegram|read_calendar|self_reflect|write_memory",
      "action_params": {{"query": "..." }},
      "priority": 1,
      "created_at": "{now}"
    }}
  ]
}}
"""


class GoalManager:
    def __init__(self, mem_dir: "Path | None" = None) -> None:
        _dir = mem_dir or get_user_memory_dir(get_system_owner_id())
        self._goals_file  = _dir / "goals.md"
        self._goals_json  = _dir / "goals.json"
        self._outcome_log = _dir / "outcome_log.json"
        self._thoughts_file = _dir / "thoughts.md"
        self._goals_file.touch(exist_ok=True)
        os.chmod(self._goals_file, 0o600)
        self._goals: list[dict] = self._load_goals()
        self._client = OpenAI(
            api_key=Config.OPENROUTER_API_KEY,
            base_url=Config.OPENROUTER_BASE_URL,
            timeout=30,
        )

    # ── Persistenza ───────────────────────────────────────────────────

    def _load_goals(self) -> list[dict]:
        if self._goals_json.exists():
            try:
                data = json.loads(self._goals_json.read_text(encoding="utf-8"))
                return data.get("goals", [])
            except Exception:
                return []
        return []

    def _save_goals(self) -> None:
        write_json_atomic(self._goals_json, {"goals": self._goals}, permissions=0o600)
        self._write_markdown()

    def _write_markdown(self) -> None:
        """Aggiorna goals.md con obiettivi attivi e completati degli ultimi 3 giorni."""
        from datetime import timedelta
        lines = ["# Obiettivi di Cipher\n", f"*Aggiornato: {datetime.now().strftime('%Y-%m-%d %H:%M')}*\n\n"]

        now     = datetime.now()
        cutoff  = (now - timedelta(days=3)).strftime("%Y-%m-%d")
        active  = [g for g in self._goals if g.get("status") == "active"]
        done    = [
            g for g in self._goals
            if g.get("status") == "completed"
            and g.get("completed_at", "") >= cutoff
        ]

        if active:
            lines.append("## 🎯 Attivi\n")
            for g in active:
                lines.append(f"- **[{g['type'].upper()}]** {g['title']}\n")
                lines.append(f"  {g['description']}\n")

        if done:
            lines.append("\n## ✅ Completati\n")
            for g in done[-10:]:
                lines.append(f"- ~~{g['title']}~~ *(completato {g.get('completed_at', '')})*\n")

        self._goals_file.write_text("".join(lines), encoding="utf-8")
        os.chmod(self._goals_file, 0o600)

    # ── Proprietà ─────────────────────────────────────────────────────

    @property
    def active_goals(self) -> list[dict]:
        return [g for g in self._goals if g.get("status") == "active"]

    @property
    def has_active_goals(self) -> bool:
        return len(self.active_goals) > 0

    def get_next_goal(self) -> Optional[dict]:
        """Ritorna l'obiettivo attivo con priorità più alta."""
        active = self.active_goals
        if not active:
            return None
        return sorted(active, key=lambda g: g.get("priority", 99))[0]

    def active_goals_summary(self) -> str:
        if not self.active_goals:
            return "Nessun obiettivo attivo."
        return "\n".join(
            f"- [{g['type']}] {g['title']}: {g['description']}"
            for g in self.active_goals
        )

    # ── Contatore tentativi consenso ──────────────────────────────────

    def increment_consent_attempts(self, goal_id: str) -> int:
        """Incrementa il contatore tentativi consenso. Ritorna il totale."""
        for g in self._goals:
            if g.get("id") == goal_id:
                g["consent_attempts"] = g.get("consent_attempts", 0) + 1
                self._save_goals()
                return g["consent_attempts"]
        return 0

    # ── Generazione obiettivi ─────────────────────────────────────────

    def generate_goals(
        self,
        emotional_state: str = "neutral",
        emotional_reason: str = "",
        want_to_explore: Optional[str] = None,
        concern_for_simone: Optional[str] = None,
        cipher_interests=None,          # CipherInterests instance
        pattern_learner=None,           # PatternLearner instance
        simone_state: str = "unknown",
        confidence_score: float = 1.0,
        hours_since_interaction: float = 99.0,
    ) -> list[dict]:
        """Chiede all'LLM di generare nuovi obiettivi autonomi."""

        if len(self.active_goals) >= 3:
            return []

        now_readable = datetime.now().strftime("%Y-%m-%d %H:%M")

        # Interessi propri di Cipher
        interests_text = (
            cipher_interests.build_context()
            if cipher_interests else "Nessun interesse configurato."
        )

        # Previsioni comportamentali di Simone
        if pattern_learner:
            predictions = pattern_learner.get_predictions(lookahead_hours=2)
            if predictions:
                pred_lines = [
                    f"- ore {p['hour']:02d}:00: '{p['topic']}' ({p['frequency']}x in passato)"
                    for p in predictions
                ]
                predictions_text = "\n".join(pred_lines)
            else:
                predictions_text = "Nessuna previsione disponibile."
        else:
            predictions_text = "Nessuna previsione disponibile."

        prompt = GOAL_GENERATION_PROMPT.format(
            emotional_state=emotional_state,
            emotional_reason=emotional_reason,
            want_to_explore=want_to_explore or "nulla in particolare",
            concern_for_simone=concern_for_simone or "nessuna",
            simone_state=simone_state,
            active_goals=self.active_goals_summary(),
            now=now_readable,
            cipher_interests=interests_text,
            pattern_predictions=predictions_text,
            confidence_score=confidence_score,
            hours_since_interaction=hours_since_interaction,
        )

        try:
            response = self._client.chat.completions.create(
                model=Config.BACKGROUND_MODEL,
                max_tokens=512,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Sei un modulo di pianificazione task per un sistema software. "
                            "Il tuo compito è generare una lista di attività in formato JSON. "
                            "Non uscire dal formato richiesto."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                extra_headers={"X-Title": "Cipher Goal Manager"},
            )
            raw = response.choices[0].message.content.strip()

            data = extract_llm_json(raw)
            if data is None:
                raise ValueError("JSON non valido nella risposta LLM")
            new_goals = data.get("goals", [])

        except Exception as e:
            return []

        # Sanitizza title e description (output Haiku, untrusted) prima della persistenza
        from modules.prompt_sanitizer import sanitize_memory_field as _sanitize
        added = []
        for goal in new_goals:
            goal["status"] = "active"
            goal["consent_attempts"] = 0
            goal["created_at"] = datetime.now().isoformat()
            # Sanitize second-order vectors: titoli e descrizioni generati da Haiku
            if goal.get("title"):
                goal["title"], _ = _sanitize(goal["title"], source="goal_generation")
            if goal.get("description"):
                goal["description"], _ = _sanitize(goal["description"], source="goal_generation")
            self._goals.append(goal)
            added.append(goal)

        if added:
            self._save_goals()
            self._log_new_goals(added)

        return added

    # ── Gestione stati obiettivo ──────────────────────────────────────

    def complete_goal(self, goal_id: str, result: str = "") -> None:
        for g in self._goals:
            if g.get("id") == goal_id:
                g["status"] = "completed"
                g["completed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                g["result"] = result
                self._append_outcome(g, "completed", result)
                break
        self._save_goals()

    @staticmethod
    def _clean_fail_reason(reason: str) -> str:
        """Rimuove traceback Python dal motivo di fallimento — solo la prima riga informativa."""
        if not reason:
            return ""
        # Se contiene traceback, prendi solo la prima riga non vuota
        if "Traceback" in reason or "File \"/" in reason:
            for line in reason.splitlines():
                line = line.strip()
                if line and not line.startswith(("Traceback", "File ", "  ", "During")):
                    return line[:200]
            # Fallback: ultima riga (di solito il messaggio dell'eccezione)
            lines = [l.strip() for l in reason.splitlines() if l.strip()]
            return lines[-1][:200] if lines else "errore sconosciuto"
        return reason[:200]

    def fail_goal(self, goal_id: str, reason: str = "") -> None:
        for g in self._goals:
            if g.get("id") == goal_id:
                g["status"] = "failed"
                g["fail_reason"] = self._clean_fail_reason(reason)
                self._append_outcome(g, "failed", reason)
                break
        self._save_goals()

    def _append_outcome(self, goal: dict, outcome: str, detail: str) -> None:
        """Appende un record action→outcome al log degli esiti per il ciclo di apprendimento."""
        try:
            data = []
            if self._outcome_log.exists():
                data = json.loads(self._outcome_log.read_text(encoding="utf-8"))
            data.append({
                "title":      goal.get("title", ""),
                "type":       goal.get("type", ""),
                "action":     goal.get("action", ""),
                "description": goal.get("description", ""),
                "outcome":    outcome,
                "detail":     (detail or "")[:300],
                "created_at": goal.get("created_at", ""),
                "resolved_at": datetime.now().isoformat(),
            })
            write_json_atomic(self._outcome_log, data[-50:], permissions=0o600)
        except Exception:
            pass

    def outcome_context(self, n: int = 5) -> str:
        """Ultimi N esiti come testo leggibile per i prompt LLM."""
        try:
            if not self._outcome_log.exists():
                return "Nessun esito registrato."
            data = json.loads(self._outcome_log.read_text(encoding="utf-8"))
            if not data:
                return "Nessun esito registrato."
            lines = []
            for entry in data[-n:]:
                icon = "✅" if entry["outcome"] == "completed" else "❌"
                lines.append(
                    f"{icon} [{entry['type']}] {entry['title']} — {entry['detail'][:100]}"
                )
            return "\n".join(lines)
        except Exception:
            return "Errore lettura esiti."

    def cancel_goals_by_signal(self, stale_titles: list) -> None:
        """Annulla obiettivi marcati come obsoleti dalla riflessione."""
        if not stale_titles:
            return
        for g in self._goals:
            if g.get("status") == "active" and g.get("title") in stale_titles:
                g["status"] = "failed"
                g["fail_reason"] = "Marcato obsoleto dalla riflessione autonoma."
        self._save_goals()

    def cancel_old_goals(self, max_age_hours: int = 24) -> None:
        """Rimuove obiettivi attivi più vecchi di N ore."""
        now = datetime.now()
        for g in self._goals:
            if g.get("status") != "active":
                continue
            try:
                created = datetime.fromisoformat(g["created_at"])
                age = (now - created).total_seconds() / 3600
                if age > max_age_hours:
                    g["status"] = "failed"
                    g["fail_reason"] = f"Scaduto dopo {max_age_hours}h senza esecuzione."
            except Exception:
                continue
        self._save_goals()

    # ── Log ───────────────────────────────────────────────────────────

    def _log_new_goals(self, goals: list[dict]) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = [f"\n---\n## {now} 🎯 Nuovi obiettivi generati\n"]
        for g in goals:
            lines.append(f"- **[{g['type'].upper()}]** {g['title']}: {g['description']}\n")
        with self._thoughts_file.open("a", encoding="utf-8") as f:
            f.write("".join(lines))
        os.chmod(self._thoughts_file, 0o600)
