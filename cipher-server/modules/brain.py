"""
modules/brain.py – Intelligenza di Cipher con memoria persistente
"""

import json
import time
from datetime import datetime
from typing import Optional, TYPE_CHECKING

from openai import OpenAI
from ddgs import DDGS
from rich.console import Console

from config import Config

if TYPE_CHECKING:
    from modules.consciousness_loop import ConsciousnessLoop

console = Console()

BEHAVIOR_DIR = Config.BASE_DIR / "comportamento"


def _build_system_prompt(memory_context: str) -> str:
    """
    Costruisce il system prompt leggendo tutti i file
    nella cartella ~/cipher/comportamento/ in ordine alfabetico.
    """
    sections = []

    if BEHAVIOR_DIR.exists():
        for fpath in sorted(BEHAVIOR_DIR.glob("*")):
            if fpath.is_file():
                try:
                    content = fpath.read_text(encoding="utf-8").strip()
                    if content:
                        sections.append(content)
                except Exception:
                    continue

    if not sections:
        sections.append("Sei Cipher, un assistente AI personale creato da Simone.")

    now = datetime.now().strftime("%A %d %B %Y, %H:%M")
    sections.append(f"Data e ora attuale: {now}")

    if memory_context:
        sections.append(memory_context)

    # Profilo motivazionale di Simone
    profile_file = Config.MEMORY_DIR / "profile.json"
    if profile_file.exists():
        try:
            profile = json.loads(profile_file.read_text(encoding="utf-8"))
            motivations = profile.get("motivations", {})
            if motivations:
                lines = ["## Cosa muove Simone (profilo motivazionale):"]
                for key, values in motivations.items():
                    if key == "aggiornato_il" or not isinstance(values, list) or not values:
                        continue
                    lines.append(f"- **{key}**: {', '.join(values)}")
                if len(lines) > 1:
                    sections.append("\n".join(lines))
        except Exception:
            pass

    # Insights sui pattern comportamentali
    insights_file = Config.MEMORY_DIR / "pattern_insights.md"
    if insights_file.exists():
        try:
            insights_text = insights_file.read_text(encoding="utf-8").strip()
            if insights_text:
                # Prende solo l'ultimo blocco (più recente)
                blocks = insights_text.split("---")
                last_block = blocks[-1].strip() if blocks else ""
                if last_block:
                    sections.append(f"## Pattern comportamentali (analisi recente):\n{last_block}")
        except Exception:
            pass

    # Note sulla voce — coerenza tra sessioni
    voice_file = Config.MEMORY_DIR / "voice_notes.md"
    if voice_file.exists():
        try:
            voice_text = voice_file.read_text(encoding="utf-8").strip()
            if voice_text:
                blocks = voice_text.split("---")
                # Prende gli ultimi 2 blocchi (ultime 2 notti)
                recent_blocks = [b.strip() for b in blocks if b.strip()][-2:]
                if recent_blocks:
                    sections.append(
                        "## Come parli — note sulla tua voce (ultime sessioni):\n"
                        + "\n---\n".join(recent_blocks)
                    )
        except Exception:
            pass

    # Contesto real-time (meteo + notizie)
    try:
        from modules.realtime_context import RealtimeContext, REALTIME_FILE
        if REALTIME_FILE.exists():
            rt = RealtimeContext()
            rt_context = rt.build_context()
            if rt_context:
                sections.append(rt_context)
    except Exception:
        pass

    # Obiettivi autonomi
    goals_file = Config.MEMORY_DIR / "goals.md"
    if goals_file.exists():
        try:
            goals_content = goals_file.read_text(encoding="utf-8").strip()
            if goals_content:
                sections.append(f"## I tuoi obiettivi autonomi\n{goals_content}")
        except Exception:
            pass

    return "\n\n".join(sections)


class Brain:
    def __init__(self) -> None:
        self._client = OpenAI(
            api_key=Config.OPENROUTER_API_KEY,
            base_url=Config.OPENROUTER_BASE_URL,
        )
        self._history: list[dict] = []
        self._ddgs = DDGS()

        from modules.memory import Memory
        self._memory = Memory()

        from modules.actions import ActionDispatcher
        self._dispatcher = ActionDispatcher(web_search_fn=self._web_search)

        # Riferimenti a moduli opzionali — impostati da ConsciousnessLoop dopo l'init
        self._consciousness:    Optional["ConsciousnessLoop"] = None
        self._impact_tracker    = None   # ImpactTracker
        self._pattern_learner   = None   # PatternLearner
        self._episodic_memory   = None   # EpisodicMemory

        # Cache system prompt
        self._system_prompt_cache: str  = ""
        self._system_prompt_ts:   float = 0.0
        self._SYSTEM_PROMPT_TTL:  float = 300.0  # 5 minuti

        console.print(
            f"[green]✓ Brain pronto[/green] "
            f"[dim](OpenRouter → {Config.OPENROUTER_MODEL})[/dim]"
        )

    # ------------------------------------------------------------------ #
    #  Web search                                                          #
    # ------------------------------------------------------------------ #

    def _web_search(self, query: str, max_results: int = 4) -> str:
        console.print(f"[cyan]🔍 Cerco:[/cyan] {query}")
        try:
            results = list(self._ddgs.text(query, max_results=max_results))
            if not results:
                return "Nessun risultato trovato."
            parts = [
                f"• {r.get('title', '')}\n  {r.get('body', '')}\n  ({r.get('href', '')})"
                for r in results
            ]
            return "Risultati ricerca:\n" + "\n\n".join(parts)
        except Exception as e:
            return f"Errore ricerca: {e}"

    # ------------------------------------------------------------------ #
    #  LLM calls                                                           #
    # ------------------------------------------------------------------ #

    def _get_system_prompt(self) -> str:
        if time.time() - self._system_prompt_ts < self._SYSTEM_PROMPT_TTL:
            return self._system_prompt_cache
        memory_ctx = self._memory.build_context()
        if self._episodic_memory:
            ep_ctx = self._episodic_memory.build_context(n=4)
            if ep_ctx:
                memory_ctx = memory_ctx + "\n\n" + ep_ctx if memory_ctx else ep_ctx
        self._system_prompt_cache = _build_system_prompt(memory_ctx)
        self._system_prompt_ts = time.time()
        return self._system_prompt_cache

    def invalidate_system_prompt(self) -> None:
        """Forza il ricalcolo al prossimo messaggio (es. dopo aggiornamento memoria)."""
        self._system_prompt_ts = 0.0

    def _build_messages(self, history: list[dict]) -> list[dict]:
        return [{"role": "system", "content": self._get_system_prompt()}] + history

    def _call_llm(self, history: list[dict]) -> str:
        for attempt in range(3):
            try:
                response = self._client.chat.completions.create(
                    model=Config.OPENROUTER_MODEL,
                    max_tokens=1024,
                    messages=self._build_messages(history),
                    extra_headers={"X-Title": "Cipher AI Assistant"},
                )
                content = response.choices[0].message.content
                return content.strip() if content else "Non ho ricevuto una risposta."
            except Exception as e:
                err = str(e).lower()
                if ("429" in err or "rate_limit" in err or "rate limit" in err) and attempt < 2:
                    wait = 20 * (attempt + 1)
                    console.print(f"[yellow]⏳ Rate limit, riprovo tra {wait}s (tentativo {attempt+1}/3)...[/yellow]")
                    time.sleep(wait)
                    continue
                console.print(f"[red]❌ LLM error: {e}[/red]")
                raise RuntimeError(f"Errore OpenRouter: {e}")

    def _call_llm_silent(self, prompt: str) -> str:
        """Chiamata background leggera — usa BACKGROUND_MODEL (Haiku).
        Per estrazione, classificazione, decisioni semplici."""
        try:
            response = self._client.chat.completions.create(
                model=Config.BACKGROUND_MODEL,
                max_tokens=256,
                messages=[{"role": "user", "content": prompt}],
                extra_headers={"X-Title": "Cipher AI Assistant"},
            )
            content = response.choices[0].message.content
            return content.strip() if content else ""
        except Exception:
            return ""

    def _call_llm_quality(self, prompt: str, max_tokens: int = 512) -> str:
        """Chiamata dove la qualità conta — usa OPENROUTER_MODEL (Sonnet).
        Per sommari, scalette, voice notes, ragionamenti profondi."""
        try:
            response = self._client.chat.completions.create(
                model=Config.OPENROUTER_MODEL,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
                extra_headers={"X-Title": "Cipher AI Assistant"},
            )
            content = response.choices[0].message.content
            return content.strip() if content else ""
        except Exception:
            return ""

    # ------------------------------------------------------------------ #
    #  JSON action helpers                                                 #
    # ------------------------------------------------------------------ #

    def _extract_action(self, text: str) -> Optional[dict]:
        """
        Estrae il primo oggetto JSON valido con chiave 'action' dal testo.
        Gestisce correttamente JSON annidati (es. params: { ... }).
        """
        start = text.find("{")
        while start != -1:
            depth = 0
            for i, ch in enumerate(text[start:], start):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = text[start:i + 1]
                        try:
                            data = json.loads(candidate)
                            if isinstance(data, dict) and "action" in data:
                                return data
                        except json.JSONDecodeError:
                            pass
                        break
            start = text.find("{", start + 1)
        return None

    def _strip_action_json(self, text: str) -> str:
        """
        Rimuove dal testo TUTTI i blocchi JSON con chiave 'action',
        in modo che non vengano mai mostrati all'utente.
        Itera finché non ne trova più.
        """
        result = text
        while True:
            start = result.find("{")
            found = False
            while start != -1:
                depth = 0
                for i, ch in enumerate(result[start:], start):
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            candidate = result[start:i + 1]
                            try:
                                data = json.loads(candidate)
                                if isinstance(data, dict) and "action" in data:
                                    result = (result[:start] + result[i + 1:]).strip()
                                    found = True
                            except json.JSONDecodeError:
                                pass
                            break
                if found:
                    break
                start = result.find("{", start + 1)
            if not found:
                break
        return result

    # ------------------------------------------------------------------ #
    #  Core think loop                                                     #
    # ------------------------------------------------------------------ #

    def think(self, user_input: str) -> str:
        if not user_input.strip():
            return "Non ho capito, puoi ripetere?"

        # Valuta l'impatto dell'ultima azione proattiva se presente
        if self._impact_tracker and self._impact_tracker.has_pending():
            self._impact_tracker.evaluate_response(user_input, brain=self)

        # Notifica la coscienza che Simone sta interagendo
        if self._consciousness:
            self._consciousness.notify_interaction()

        # Controlla se c'è un consenso pendente dalla coscienza autonoma
        if self._consciousness:
            consent_response = self._consciousness.handle_consent_response(user_input)
            if consent_response is not None:
                self._memory.add_message("user", user_input)
                self._memory.add_message("assistant", consent_response)
                return consent_response

        # Se c'è un'azione in attesa di consenso del dispatcher, gestiscila
        if self._dispatcher.has_pending():
            response = self._dispatcher.check_consent(user_input)
            if response is not None:
                self._memory.add_message("user", user_input)
                self._memory.add_message("assistant", response)
                return response

        forget_resp = self._memory.handle_forget_command(user_input)
        if forget_resp:
            return forget_resp

        remember_resp = self._memory.handle_remember_command(user_input)
        if remember_resp:
            self._memory.add_message("user", user_input)
            self._memory.add_message("assistant", remember_resp)
            return remember_resp

        self._history.append({"role": "user", "content": user_input})
        self._memory.add_message("user", user_input)

        if len(self._history) > Config.MAX_HISTORY_MESSAGES * 2:
            self._history = self._history[-(Config.MAX_HISTORY_MESSAGES * 2):]

        # Estrazione memoria e pattern — in thread separato, con delay per non competere con la risposta
        import threading, time as _t
        def _background_tasks():
            _t.sleep(10)  # aspetta che la risposta principale sia completata
            self._memory.extract_from_message(user_input, self._call_llm_silent)
            if self._pattern_learner:
                try:
                    now = datetime.now()
                    topic = self._call_llm_silent(
                        f"In 2-3 parole, qual è l'argomento principale di questo messaggio? "
                        f"Solo le parole, nient'altro.\nMessaggio: {user_input[:200]}"
                    )
                    if topic:
                        self._pattern_learner.record_interaction(now.hour, now.weekday(), topic.strip())
                except Exception:
                    pass
        threading.Thread(target=_background_tasks, daemon=True).start()

        try:
            raw = self._call_llm(self._history)
        except RuntimeError as e:
            err = str(e).lower()
            if "insufficient" in err or "credit" in err or "quota" in err or "billing" in err or "402" in err:
                return "Non riesco a risponderti in questo momento — i crediti API sono esauriti. Ricarica l'account per continuare."
            if "429" in err or "rate_limit" in err or "rate limit" in err:
                return "Sto ricevendo troppe richieste al minuto, aspetta qualche secondo e riprova."
            if "401" in err or "authentication" in err or "api key" in err:
                return "C'è un problema con la chiave API — non riesco ad autenticarmi. Controlla la configurazione nel .env."
            if "timeout" in err or "timed out" in err:
                return "La richiesta ha impiegato troppo tempo e ho dovuto interromperla. Riprova tra poco."
            if "connection" in err or "network" in err or "503" in err or "502" in err:
                return "Non riesco a raggiungere il server in questo momento. Controlla la connessione."
            return "Si è verificato un errore e non riesco a risponderti adesso. Riprova tra poco."

        action_data = self._extract_action(raw)
        if action_data:
            action = action_data.get("action", "")
            params = action_data.get("params", {})
            action_result = self._dispatcher.execute(action, params)

            if self._dispatcher.has_pending():
                raw_clean = self._strip_action_json(raw)
                self._history.append({"role": "assistant", "content": raw_clean})
                self._memory.add_message("assistant", action_result)
                return action_result

            raw_clean = self._strip_action_json(raw)
            augmented = self._history + [
                {"role": "assistant", "content": raw_clean},
                {
                    "role": "user",
                    "content": (
                        f"[RISULTATO '{action}']\n{action_result}\n\n"
                        "Rispondi in modo naturale, senza mostrare JSON o blocchi tecnici."
                    ),
                },
            ]
            raw = self._strip_action_json(self._call_llm(augmented))
        else:
            raw = self._strip_action_json(raw)

        # Aggiunge promemoria consenso pendente in fondo alla risposta
        if self._consciousness:
            reminder = self._consciousness.pending_consent_reminder()
            if reminder:
                raw = raw + reminder

        self._history.append({"role": "assistant", "content": raw})
        self._memory.add_message("assistant", raw)
        return raw

    # ------------------------------------------------------------------ #
    #  Utility                                                             #
    # ------------------------------------------------------------------ #

    def reset(self) -> None:
        self._history.clear()
        console.print("[yellow]↺ Sessione resettata (la memoria rimane)[/yellow]")

    def reset_memory(self) -> None:
        self._history.clear()
        self._memory.handle_forget_command("dimentica tutto")

    @property
    def history_length(self) -> int:
        return len(self._history)
