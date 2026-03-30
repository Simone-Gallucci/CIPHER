"""
modules/brain.py – Intelligenza di Cipher con memoria persistente
"""

import json
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

    def _build_messages(self, history: list[dict]) -> list[dict]:
        memory_ctx = self._memory.build_context()
        # Aggiunge contesto episodico se disponibile
        if self._episodic_memory:
            ep_ctx = self._episodic_memory.build_context(n=4)
            if ep_ctx:
                memory_ctx = memory_ctx + "\n\n" + ep_ctx if memory_ctx else ep_ctx
        system = _build_system_prompt(memory_ctx)
        return [{"role": "system", "content": system}] + history

    def _call_llm(self, history: list[dict]) -> str:
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
            raise RuntimeError(f"Errore OpenRouter: {e}")

    def _call_llm_silent(self, prompt: str) -> str:
        try:
            response = self._client.chat.completions.create(
                model=Config.OPENROUTER_MODEL,
                max_tokens=256,
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

        self._memory.extract_from_message(user_input, self._call_llm_silent)

        # Registra pattern comportamentale (argomento + ora + giorno)
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

        raw = self._call_llm(self._history)

        action_data = self._extract_action(raw)
        if action_data:
            action = action_data.get("action", "")
            params = action_data.get("params", {})
            action_result = self._dispatcher.execute(action, params)

            if self._dispatcher.has_pending():
                raw_clean = self._strip_action_json(raw)
                self._history.append({"role": "assistant", "content": raw_clean})
                self._memory.add_message("user", user_input)
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
