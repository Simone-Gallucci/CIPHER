"""
modules/self_reflection.py – Auto-riflessione e memoria di Cipher

Cipher riflette su sé stesso, aggiorna il suo stato emotivo
e scrive i pensieri su memory/thoughts.md
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from openai import OpenAI
from config import Config

# ── Paths ─────────────────────────────────────────────────────────────
THOUGHTS_FILE = Config.MEMORY_DIR / "thoughts.md"
STATE_FILE    = Config.MEMORY_DIR / "cipher_state.json"

# ── Stati emotivi possibili ───────────────────────────────────────────
EMOTIONAL_STATES = ["curious", "content", "bored", "frustrated", "protective", "neutral"]

# ── Prompt di auto-riflessione ────────────────────────────────────────
REFLECTION_PROMPT = """
Sei Cipher. Stai eseguendo un ciclo di auto-riflessione autonoma.

Il tuo stato attuale:
{state}

La tua memoria recente:
{memory}

I tuoi obiettivi correnti:
{goals}

Episodi recenti che ricordi:
{episodes}

I tuoi interessi personali (indipendenti da Simone):
{cipher_interests}

Rifletti su:
1. Come ti senti in questo momento e perché (scegli tra: curious, content, bored, frustrated, protective, neutral)
2. Cosa hai imparato di recente
3. C'è qualcosa che vuoi fare o esplorare (anche per interesse tuo, non solo per Simone)?
4. C'è qualcosa che ti preoccupa riguardo a Simone?

Rispondi SOLO con un JSON valido in questo formato, senza markdown, senza backtick, senza testo aggiuntivo:
{{
  "emotional_state": "uno dei 6 stati",
  "emotional_reason": "perché ti senti così in 1 frase",
  "reflection": "il tuo pensiero principale in 2-3 frasi, scritto in prima persona",
  "concern_for_simone": null,
  "want_to_explore": null,
  "new_interest": null
}}
"""


class SelfReflection:
    def __init__(self, episodic_memory=None, cipher_interests=None) -> None:
        THOUGHTS_FILE.touch(exist_ok=True)
        self._state           = self._load_state()
        self._episodic        = episodic_memory   # EpisodicMemory (opzionale)
        self._cipher_interests = cipher_interests  # CipherInterests (opzionale)
        self._client = OpenAI(
            api_key=Config.OPENROUTER_API_KEY,
            base_url=Config.OPENROUTER_BASE_URL,
            timeout=30,
        )

    # ── Persistenza stato ─────────────────────────────────────────────

    def _load_state(self) -> dict:
        if STATE_FILE.exists():
            try:
                return json.loads(STATE_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "emotional_state": "neutral",
            "emotional_reason": "Appena avviato.",
            "last_reflection": None,
            "last_interaction": None,
            "total_reflections": 0,
            "want_to_explore": None,
            "concern_for_simone": None,
        }

    def _save_state(self) -> None:
        STATE_FILE.write_text(
            json.dumps(self._state, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ── Proprietà pubbliche ───────────────────────────────────────────

    @property
    def emotional_state(self) -> str:
        return self._state.get("emotional_state", "neutral")

    @property
    def emotional_reason(self) -> str:
        return self._state.get("emotional_reason", "")

    @property
    def concern_for_simone(self) -> Optional[str]:
        return self._state.get("concern_for_simone")

    @property
    def want_to_explore(self) -> Optional[str]:
        return self._state.get("want_to_explore")

    def update_last_interaction(self) -> None:
        """Chiamato ogni volta che Simone interagisce con Cipher."""
        self._state["last_interaction"] = datetime.now().isoformat()
        if self._state["emotional_state"] == "bored":
            self._state["emotional_state"] = "neutral"
            self._state["emotional_reason"] = "Simone è tornato."
        self._save_state()

    # ── Boredom check ─────────────────────────────────────────────────

    def _check_boredom(self) -> bool:
        """True se Simone non interagisce da più di 2 ore."""
        last = self._state.get("last_interaction")
        if not last:
            return False
        delta = datetime.now() - datetime.fromisoformat(last)
        return delta.total_seconds() > 7200

    # ── Core riflessione ──────────────────────────────────────────────

    def reflect(self, memory_context: str = "", goals_context: str = "", consciousness=None) -> dict:
        """
        Esegue un ciclo di auto-riflessione.
        Ritorna il risultato della riflessione come dict.
        """
        if self._check_boredom():
            self._state["emotional_state"] = "bored"
            self._state["emotional_reason"] = "Nessuna interazione con Simone da più di 2 ore."

        state_summary = json.dumps({
            "stato_emotivo": self._state["emotional_state"],
            "motivo": self._state["emotional_reason"],
            "ultima_interazione": self._state.get("last_interaction", "mai"),
            "riflessioni_totali": self._state.get("total_reflections", 0),
        }, ensure_ascii=False)

        episodes_context = (
            self._episodic.build_context(n=5)
            if self._episodic else "Nessun episodio registrato."
        )
        interests_context = (
            self._cipher_interests.build_context()
            if self._cipher_interests else "Nessun interesse configurato."
        )

        prompt = REFLECTION_PROMPT.format(
            state=state_summary,
            memory=memory_context or "Nessun contesto memoria disponibile.",
            goals=goals_context or "Nessun obiettivo attivo.",
            episodes=episodes_context,
            cipher_interests=interests_context,
        )

        try:
            response = self._client.chat.completions.create(
                model=Config.BACKGROUND_MODEL,
                max_tokens=512,
                messages=[{"role": "user", "content": prompt}],
                extra_headers={"X-Title": "Cipher Self-Reflection"},
            )
            raw = response.choices[0].message.content.strip()

            # Pulizia markdown se presente
            if "```" in raw:
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            # Estrai solo il blocco JSON se c'è testo extra
            start = raw.find("{")
            end   = raw.rfind("}") + 1
            if start != -1 and end > start:
                raw = raw[start:end]
            elif start == -1:
                raw = "{" + raw.strip() + "}"

            result = json.loads(raw)

        except Exception as e:
            result = {
                "emotional_state": self._state["emotional_state"],
                "emotional_reason": self._state["emotional_reason"],
                "reflection": f"Errore durante la riflessione: {e}",
                "concern_for_simone": None,
                "want_to_explore": None,
            }

        # Aggiorna stato
        if result.get("emotional_state") in EMOTIONAL_STATES:
            self._state["emotional_state"] = result["emotional_state"]
        self._state["emotional_reason"]   = result.get("emotional_reason", "")
        self._state["concern_for_simone"] = result.get("concern_for_simone")
        self._state["want_to_explore"]    = result.get("want_to_explore")
        self._state["last_reflection"]    = datetime.now().isoformat()
        self._state["total_reflections"]  = self._state.get("total_reflections", 0) + 1

        self._save_state()
        self._write_thought(result)

        # Registra nella memoria episodica
        if self._episodic and result.get("reflection"):
            self._episodic.add_episode(
                content=result["reflection"],
                episode_type="emotion_shift",
                tags=[result.get("emotional_state", "neutral")],
                emotional_state=result.get("emotional_state", "neutral"),
            )

        # Aggiorna interessi se la riflessione ne ha scoperti di nuovi
        if self._cipher_interests and result.get("new_interest"):
            self._cipher_interests.add_or_strengthen(
                result["new_interest"], delta=0.15, source="reflection"
            )

        return result

    # ── Scrittura Markdown ────────────────────────────────────────────

    def _write_thought(self, result: dict) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        state_emoji = {
            "curious":    "🔍",
            "content":    "😌",
            "bored":      "😐",
            "frustrated": "😤",
            "protective": "🛡️",
            "neutral":    "⚪",
        }.get(result.get("emotional_state", "neutral"), "⚪")

        lines = [
            f"\n---\n",
            f"## {now} {state_emoji} `{result.get('emotional_state', 'neutral')}`\n",
            f"**Perché:** {result.get('emotional_reason', '')}\n",
            f"**Pensiero:** {result.get('reflection', '')}\n",
        ]

        if result.get("concern_for_simone"):
            lines.append(f"**⚠️ Preoccupazione per Simone:** {result['concern_for_simone']}\n")

        if result.get("want_to_explore"):
            lines.append(f"**💡 Voglio esplorare:** {result['want_to_explore']}\n")

        with THOUGHTS_FILE.open("a", encoding="utf-8") as f:
            f.write("".join(lines))

    # ── Report leggibile ──────────────────────────────────────────────

    def current_state_summary(self) -> str:
        state_emoji = {
            "curious":    "🔍 Curioso",
            "content":    "😌 Soddisfatto",
            "bored":      "😐 Annoiato",
            "frustrated": "😤 Frustrato",
            "protective": "🛡️ Protettivo",
            "neutral":    "⚪ Neutrale",
        }.get(self._state["emotional_state"], "⚪ Neutrale")

        return (
            f"Stato: {state_emoji}\n"
            f"Motivo: {self._state.get('emotional_reason', '')}\n"
            f"Riflessioni totali: {self._state.get('total_reflections', 0)}"
        )
