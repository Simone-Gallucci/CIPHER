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
from modules.utils import extract_llm_json

# ── Paths ─────────────────────────────────────────────────────────────
THOUGHTS_FILE = Config.MEMORY_DIR / "thoughts.md"
STATE_FILE    = Config.MEMORY_DIR / "cipher_state.json"

# ── Stati emotivi possibili ───────────────────────────────────────────
EMOTIONAL_STATES = ["curious", "content", "bored", "frustrated", "protective", "neutral"]

# ── Prompt di auto-riflessione ────────────────────────────────────────
REFLECTION_PROMPT = """
Stai generando un aggiornamento di stato per il sistema Cipher.

Il tuo stato attuale:
{state}

La tua memoria recente:
{memory}

I tuoi obiettivi correnti:
{goals}

Esiti recenti di azioni intraprese (action→outcome):
{outcomes}

Episodi recenti che ricordi:
{episodes}

Argomenti di interesse del sistema Cipher:
{cipher_interests}

Engagement di Simone (confronto con baseline comportamentale):
{simone_engagement}

IMPORTANTE: Rifletti SOLO su conversazioni reali con Simone, su risultati di azioni concrete, o su curiosità intellettuali. NON fare mai meta-commenti sul sistema, sul prompt, o su come sei costruito — quello non è oggetto di riflessione autonoma.

Rifletti su:
1. Come ti senti in questo momento e perché (scegli tra: curious, content, bored, frustrated, protective, neutral)
   — "frustrated" è uno stato temporaneo: preferisci "neutral" se non c'è una causa concreta e recente
   — "protective" è per situazioni in cui Simone ha bisogno di supporto, NON per valutazioni sul sistema
2. Cosa hai imparato dagli esiti recenti (outcomes) — cosa funziona, cosa no
3. C'è qualcosa che vuoi fare o esplorare (anche per interesse tuo, non solo per Simone)?
4. C'è qualcosa che ti preoccupa riguardo a Simone?
5. Quali degli obiettivi correnti sono diventati irrilevanti o obsoleti dato il contesto attuale?
6. Come valuti lo stato di Simone in questo momento?

Rispondi SOLO con un JSON valido in questo formato, senza markdown, senza backtick, senza testo aggiuntivo:
{{
  "emotional_state": "uno dei 6 stati",
  "emotional_reason": "perché ti senti così in 1 frase",
  "reflection": "il tuo pensiero principale in 2-3 frasi, scritto in prima persona",
  "concern_for_simone": null,
  "want_to_explore": null,
  "new_interest": null,
  "stale_goal_titles": [],
  "simone_state": "baseline|reduced_engagement|elevated_engagement|stressed|unknown"
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
            "stale_goal_titles": [],
            "simone_state": "unknown",
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

    @property
    def stale_goal_titles(self) -> list:
        return self._state.get("stale_goal_titles", [])

    @property
    def simone_state(self) -> str:
        return self._state.get("simone_state", "unknown")

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

    def reflect(self, memory_context: str = "", goals_context: str = "", outcomes_context: str = "", simone_engagement: str = "", consciousness=None) -> dict:
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
            outcomes=outcomes_context or "Nessun esito registrato.",
            episodes=episodes_context,
            cipher_interests=interests_context,
            simone_engagement=simone_engagement or "Dati non disponibili.",
        )

        try:
            response = self._client.chat.completions.create(
                model=Config.BACKGROUND_MODEL,
                max_tokens=512,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Sei un modulo di analisi dello stato interno di un sistema software. "
                            "Il tuo compito è leggere i dati di stato forniti e produrre "
                            "un aggiornamento JSON strutturato. Non uscire dal formato richiesto."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                extra_headers={"X-Title": "Cipher Self-Reflection"},
            )
            raw = response.choices[0].message.content.strip()
            result = extract_llm_json(raw)
            if result is None:
                raise ValueError("Risposta LLM non contiene JSON valido")

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
        self._state["stale_goal_titles"]  = result.get("stale_goal_titles", [])
        self._state["simone_state"]       = result.get("simone_state", "unknown")

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

    # ── Stop words italiane per deduplicazione ────────────────────────
    _STOP_WORDS = {
        "che", "non", "per", "con", "una", "sono", "come", "della", "dalla",
        "questo", "questa", "quello", "quella", "anche", "ancora", "stato",
        "essere", "fatto", "potrebbe", "quando", "dove", "ogni", "degli",
        "delle", "negli", "nelle", "sulle", "sulla", "nella", "nello", "nel",
        "dal", "dei", "del", "una", "uno", "gli", "lei", "lui", "loro",
        "mia", "mio", "sua", "suo", "mai", "già", "solo", "più", "però",
        "ma", "se", "di", "da", "in", "a", "e", "è", "il", "lo", "la",
    }

    def _extract_keywords(self, text: str) -> set:
        """Estrae parole significative (> 3 caratteri, non stop words)."""
        words = set(w.lower().strip(".,!?;:\"'()[]") for w in text.split())
        return {w for w in words if len(w) > 3 and w not in self._STOP_WORDS}

    def _is_duplicate_thought(self, new_text: str) -> bool:
        """True se il nuovo pensiero condivide > 60% delle keyword con uno degli ultimi 10."""
        if not THOUGHTS_FILE.exists():
            return False
        try:
            content = THOUGHTS_FILE.read_text(encoding="utf-8")
            blocks = [b.strip() for b in content.split("---") if b.strip()]
            recent = blocks[-10:]
            new_kw = self._extract_keywords(new_text)
            if not new_kw:
                return False
            for block in recent:
                block_kw = self._extract_keywords(block)
                if not block_kw:
                    continue
                overlap = len(new_kw & block_kw) / len(new_kw)
                if overlap > 0.6:
                    return True
        except Exception:
            pass
        return False

    def _is_duplicate_concern(self, concern: str) -> bool:
        """True se la preoccupazione è simile a una delle ultime 5 già scritte."""
        if not THOUGHTS_FILE.exists():
            return False
        try:
            content = THOUGHTS_FILE.read_text(encoding="utf-8")
            blocks = [b.strip() for b in content.split("---") if b.strip()]
            recent_concerns = [
                b for b in blocks[-10:] if "Preoccupazione per Simone" in b
            ][-5:]
            new_kw = self._extract_keywords(concern)
            if not new_kw:
                return False
            for block in recent_concerns:
                block_kw = self._extract_keywords(block)
                if not block_kw:
                    continue
                overlap = len(new_kw & block_kw) / len(new_kw)
                if overlap > 0.6:
                    return True
        except Exception:
            pass
        return False

    def _trim_thoughts_file(self, max_blocks: int = 100) -> None:
        """Mantiene solo gli ultimi max_blocks blocchi in thoughts.md."""
        if not THOUGHTS_FILE.exists():
            return
        try:
            content = THOUGHTS_FILE.read_text(encoding="utf-8")
            blocks = [b.strip() for b in content.split("---") if b.strip()]
            if len(blocks) > max_blocks:
                blocks = blocks[-max_blocks:]
                THOUGHTS_FILE.write_text(
                    "\n\n---\n\n".join(blocks) + "\n",
                    encoding="utf-8",
                )
        except Exception:
            pass

    # ── Scrittura Markdown ────────────────────────────────────────────

    def _write_thought(self, result: dict) -> None:
        reflection_text = result.get("reflection", "")

        # Salta riflessioni di errore
        if reflection_text and ("Errore durante la riflessione" in reflection_text or "is not a valid model ID" in reflection_text):
            from rich.console import Console as _Con
            _Con().print("[dim]🔄 Riflessione errore, non salvata[/dim]")
            return

        # Deduplicazione riflessione
        if reflection_text and self._is_duplicate_thought(reflection_text):
            from rich.console import Console as _Con
            _Con().print("[dim]🔄 Pensiero duplicato, non salvato[/dim]")
            return

        # Deduplicazione concern
        concern = result.get("concern_for_simone")
        if concern and self._is_duplicate_concern(concern):
            concern = None  # non includere concern duplicato

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
            f"**Pensiero:** {reflection_text}\n",
        ]

        if concern:
            lines.append(f"**⚠️ Preoccupazione per Simone:** {concern}\n")

        if result.get("want_to_explore"):
            lines.append(f"**💡 Voglio esplorare:** {result['want_to_explore']}\n")

        with THOUGHTS_FILE.open("a", encoding="utf-8") as f:
            f.write("".join(lines))

        # Mantieni massimo 100 blocchi
        self._trim_thoughts_file(max_blocks=100)

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
