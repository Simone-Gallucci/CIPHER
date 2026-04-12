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
from modules.utils import extract_action_json, write_json_atomic

if TYPE_CHECKING:
    from modules.consciousness_loop import ConsciousnessLoop

console = Console()

BEHAVIOR_DIR = Config.BASE_DIR / "comportamento"

# Cache per file statici: path assoluto → (mtime, contenuto)
_file_cache: dict[str, tuple[float, str]] = {}


def _read_cached(path: "Path") -> str:
    """Legge un file usando la cache mtime — rilegge solo se il file è cambiato."""
    key = str(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return ""
    cached = _file_cache.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        content = path.read_text(encoding="utf-8")
        _file_cache[key] = (mtime, content)
        return content
    except Exception:
        return ""


def _build_system_prompt(memory_context: str, history: "list[dict] | None" = None, static_prompt: str = "") -> str:
    """
    Assembla il system prompt a partire dal prompt statico (comportamento/)
    già caricato all'avvio, più le sezioni dinamiche (memoria, obiettivi, ecc.).
    """
    sections = []

    if static_prompt:
        sections.append(static_prompt)
    else:
        sections.append("Sei Cipher, un assistente AI personale creato da Simone.")

    now = datetime.now().strftime("%A %d %B %Y, %H:%M")
    sections.append(f"Data e ora attuale: {now}")
    sections.append(
        "REGOLE FONDAMENTALI DI COMPORTAMENTO:\n"
        "1. Non inventare MAI informazioni, dettagli o riferimenti a cose non presenti nel contesto o nella conversazione.\n"
        "2. Se non sai qualcosa, non menzionarla. Non dedurre, non completare, non immaginare.\n"
        "3. Non ripetere domande o argomenti a cui Simone ha già risposto. Se ha risposto, quell'argomento è chiuso.\n"
        "4. Se non hai niente di specifico o utile da dire, sii breve e naturale. Non forzare conversazioni.\n"
        "5. Quando scrivi messaggi proattivi, NON fare riferimento a cose che non sai con certezza in questo momento.\n"
        "6. Tratta le informazioni nella memoria con senso del tempo: qualcosa di 3+ giorni fa è vecchio, non usarlo come se fosse attuale.\n"
        "7. Scrivi in italiano naturale. Se una frase suona tradotta o artificiale, riformulala in modo più semplice. Meglio una frase banale ma corretta che una elaborata ma sbagliata."
    )

    if memory_context:
        sections.append(memory_context)

    # Profilo motivazionale di Simone
    profile_file = Config.MEMORY_DIR / "profile.json"
    if profile_file.exists():
        try:
            profile = json.loads(_read_cached(profile_file))
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
            insights_text = _read_cached(insights_file).strip()
            if insights_text:
                # Prende solo l'ultimo blocco (più recente)
                blocks = insights_text.split("---")
                last_block = blocks[-1].strip() if blocks else ""
                if last_block and len(last_block) < 500:
                    sections.append(f"## Pattern comportamentali (analisi recente):\n{last_block}")
        except Exception:
            pass

    # Note sulla voce — coerenza tra sessioni
    voice_file = Config.MEMORY_DIR / "voice_notes.md"
    if voice_file.exists():
        try:
            voice_text = _read_cached(voice_file).strip()
            if voice_text:
                blocks = voice_text.split("---")
                # Prende gli ultimi 2 blocchi (ultime 2 notti)
                recent_blocks = [b.strip() for b in blocks if b.strip()][-1:]
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
            goals_content = _read_cached(goals_file).strip()
            if goals_content:
                sections.append(f"## I tuoi obiettivi autonomi\n{goals_content}")
        except Exception:
            pass

    # Memoria emotiva di Simone (ultime voci, deduplicate)
    emotional_log = Config.MEMORY_DIR / "emotional_log.json"
    if emotional_log.exists():
        try:
            entries = json.loads(_read_cached(emotional_log))
            if entries:
                # Deduplicazione per (state, note[:35]): evita dominanza di uno stato ripetuto
                seen_keys: set = set()
                unique: list = []
                for e in reversed(entries[-10:]):
                    k = (e.get("state", ""), e.get("note", "")[:35])
                    if k not in seen_keys:
                        seen_keys.add(k)
                        unique.append(e)
                    if len(unique) == 3:
                        break
                recent = list(reversed(unique))
                lines = [f"  [{e['timestamp'][:16]}] {e['state']} — {e['note']}" for e in recent]
                sections.append("## Stato emotivo recente di Simone:\n" + "\n".join(lines))
        except Exception:
            pass

    # Feedback implicito — calibrazione azioni autonome
    # (commentato: aggiungeva contesto non necessario che contribuiva alle allucinazioni)
    # feedback_file = Config.MEMORY_DIR / "feedback_weights.json"
    # if feedback_file.exists():
    #     try:
    #         weights = json.loads(_read_cached(feedback_file))
    #         if weights:
    #             lines = [f"  {k}: {v['score']:.2f} ({v['samples']} campioni)" for k, v in weights.items()]
    #             sections.append("## Efficacia azioni autonome (feedback implicito):\n" + "\n".join(lines))
    #     except Exception:
    #         pass

    # Script approvati disponibili
    try:
        from modules.script_registry import REGISTRY_FILE
        if REGISTRY_FILE.exists():
            registry = json.loads(_read_cached(REGISTRY_FILE))
            approved = [
                (name, entry.get("description", "").strip())
                for name, entry in registry.get("scripts", {}).items()
                if entry.get("approved") and entry.get("description", "").strip()
            ]
            if approved:
                lines = [f'- {name}: "{desc}"' for name, desc in approved]
                sections.append(
                    "## Script approvati disponibili (usa shell_exec con il nome dello script):\n"
                    + "\n".join(lines)
                )
    except Exception:
        pass

    # Dev protocol — caricato solo se Simone sta parlando di sviluppo Cipher
    _DEV_KEYWORDS = {
        "project_read", "project_write", "project_list",
        "modifica", "modifica cipher", "bug", "fix",
        "codice", "modulo", "script", "cipher-server",
        "brain.py", "consciousness", "aggiorna cipher", "deploy"
    }
    if history:
        recent_user_texts = [
            m.get("content", "").lower()
            for m in history
            if m.get("role") == "user"
        ][-5:]
        if any(kw in text for text in recent_user_texts for kw in _DEV_KEYWORDS):
            dev_protocol_path = Config.BASE_DIR / "config" / "dev_protocol.txt"
            if dev_protocol_path.exists():
                dev_content = _read_cached(dev_protocol_path).strip()
                if dev_content:
                    sections.append(dev_content)
                    console.print("[dim]📋 Dev protocol caricato nel contesto[/dim]")

    total = "\n\n".join(sections)
    if len(total) > 4000:
        console.print(f"[yellow]⚠️ System prompt lungo: {len(total)} caratteri[/yellow]")
    return total


class Brain:
    def __init__(self) -> None:
        self._client = OpenAI(
            api_key=Config.OPENROUTER_API_KEY,
            base_url=Config.OPENROUTER_BASE_URL,
        )
        self._history: list[dict] = []
        self._history_times: list[str] = []
        self._load_history()
        self._ddgs = DDGS()

        # Prompt statico (comportamento/) — caricato una sola volta all'avvio
        self._static_prompt: str = self._load_static_prompt()

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

        # Sampling topic per PatternLearner: LLM call 1 ogni 3 messaggi
        self._topic_sample_counter: int = 0

        # Stato pendente per il comando Tabula Rasa
        self._tabula_rasa_pending: bool = False

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

    def _load_static_prompt(self) -> str:
        """Legge tutti i file in comportamento/ una sola volta all'avvio."""
        parts = []
        if BEHAVIOR_DIR.exists():
            for fpath in sorted(BEHAVIOR_DIR.glob("*")):
                if fpath.is_file():
                    content = _read_cached(fpath).strip()
                    if content:
                        parts.append(content)
        result = "\n\n".join(parts) if parts else "Sei Cipher, un assistente AI personale creato da Simone."
        console.print(f"[dim]📋 Prompt statico caricato: {len(result)} caratteri da {len(parts)} file[/dim]")
        return result

    def reload_static_prompt(self) -> None:
        """Ricarica manualmente il prompt statico (dopo modifica dei file in comportamento/)."""
        self._static_prompt = self._load_static_prompt()
        self.invalidate_system_prompt()
        console.print("[green]📋 Prompt statico ricaricato[/green]")

    def _get_system_prompt(self) -> str:
        if time.time() - self._system_prompt_ts < self._SYSTEM_PROMPT_TTL:
            return self._system_prompt_cache
        memory_ctx = self._memory.build_context()
        if self._episodic_memory:
            ep_ctx = self._episodic_memory.build_context(n=4)
            if ep_ctx:
                memory_ctx = memory_ctx + "\n\n" + ep_ctx if memory_ctx else ep_ctx
        self._system_prompt_cache = _build_system_prompt(memory_ctx, self._history, self._static_prompt)
        self._system_prompt_ts = time.time()
        return self._system_prompt_cache

    def invalidate_system_prompt(self) -> None:
        """Forza il ricalcolo al prossimo messaggio (es. dopo aggiornamento memoria)."""
        self._system_prompt_ts = 0.0

    def _build_messages(self, history: list[dict]) -> list[dict]:
        system_content = self._get_system_prompt()
        if self._consciousness and self._consciousness.brief_sent_today():
            system_content += (
                "\n\nNota di contesto: hai già mandato il morning brief a Simone oggi. "
                "Se ti saluta ('buongiorno', 'ciao', ecc.), rispondi normalmente — "
                "come un amico che si è già sentito stamattina, non come se fosse la prima interazione della giornata."
            )
        return [{"role": "system", "content": system_content}] + history

    def _call_llm(self, history: list[dict], image_b64: Optional[str] = None, media_type: str = "image/jpeg") -> str:
        messages = self._build_messages(history)
        if image_b64:
            # Inject image into the last user message as multimodal content
            for i in range(len(messages) - 1, -1, -1):
                if messages[i]["role"] == "user":
                    text = messages[i]["content"]
                    messages[i] = {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{image_b64}"}},
                            {"type": "text", "text": text},
                        ],
                    }
                    break
        for attempt in range(3):
            try:
                response = self._client.chat.completions.create(
                    model=Config.OPENROUTER_MODEL,
                    max_tokens=1024,
                    temperature=0.4,
                    messages=messages,
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
                temperature=0.2,
                messages=[
                    {"role": "system", "content": self._get_system_prompt()},
                    {"role": "user",   "content": prompt},
                ],
                extra_headers={"X-Title": "Cipher AI Assistant"},
            )
            content = response.choices[0].message.content
            return content.strip() if content else ""
        except Exception:
            return ""

    def _call_llm_visible(self, prompt: str, max_tokens: int = 512) -> str:
        """Chiamata per messaggi visibili a Simone — usa OPENROUTER_MODEL (Sonnet).
        Per check-in, morning brief, reminder, e qualsiasi testo che Simone leggerà."""
        try:
            response = self._client.chat.completions.create(
                model=Config.OPENROUTER_MODEL,
                max_tokens=max_tokens,
                temperature=0.4,
                messages=[
                    {"role": "system", "content": self._get_system_prompt()},
                    {"role": "user",   "content": prompt},
                ],
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
                temperature=0.5,
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
        return extract_action_json(text)

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

    def think(self, user_input: str, image_b64: Optional[str] = None, media_type: str = "image/jpeg") -> str:
        if not user_input.strip() and not image_b64:
            return "Non ho capito, puoi ripetere?"

        # Valuta l'impatto dell'ultima azione proattiva se presente
        if self._impact_tracker and self._impact_tracker.has_pending():
            self._impact_tracker.evaluate_response(user_input, brain=self)

        # Notifica la coscienza che Simone sta interagendo
        if self._consciousness:
            self._consciousness.notify_interaction()

        # ── Tabula Rasa ──────────────────────────────────────────────────────
        _tr_msg = user_input.strip().lower()
        if not self._tabula_rasa_pending and (
            "tabula rasa" in _tr_msg or _tr_msg == "/tabularasa"
        ):
            self._tabula_rasa_pending = True
            self._memory.add_message("user", user_input)
            resp = "Attivare protocollo Tabula Rasa?"
            self._memory.add_message("assistant", resp)
            return resp

        if self._tabula_rasa_pending:
            _tr_consent = {"sì", "si", "ok", "confermo", "yes", "vai", "fallo"}
            if _tr_msg in _tr_consent:
                self._execute_tabula_rasa()
                self._tabula_rasa_pending = False
                resp = "Protocollo Tabula Rasa eseguito."
                self._memory.add_message("assistant", resp)
                return resp
            else:
                self._tabula_rasa_pending = False
                resp = "Ok, annullato. Tutto resta com'è."
                self._memory.add_message("user", user_input)
                self._memory.add_message("assistant", resp)
                return resp
        # ─────────────────────────────────────────────────────────────────────

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

        now_ts = datetime.now().strftime("%d/%m/%Y %H:%M")
        self._history.append({"role": "user", "content": f"[{now_ts}] {user_input}"})
        self._history_times.append(datetime.now().isoformat())
        self._memory.add_message("user", user_input)

        if len(self._history) > Config.MAX_HISTORY_MESSAGES * 2:
            self._history       = self._history[-(Config.MAX_HISTORY_MESSAGES * 2):]
            self._history_times = self._history_times[-(Config.MAX_HISTORY_MESSAGES * 2):]

        # Estrazione memoria e pattern — in thread separato, con delay per non competere con la risposta
        import threading, time as _t
        _user_input_snapshot = user_input
        _history_snapshot    = list(self._history)

        def _background_tasks():
            _t.sleep(10)  # aspetta che la risposta principale sia completata
            self._memory.extract_from_message(_user_input_snapshot, self._call_llm_silent)

            if self._pattern_learner:
                try:
                    now_bg = datetime.now()
                    self._topic_sample_counter += 1
                    if self._topic_sample_counter % 3 == 0:
                        # LLM call 1 su 3 per estrarre topic preciso
                        topic = self._call_llm_silent(
                            f"In 2-3 parole, qual è l'argomento principale di questo messaggio? "
                            f"Solo le parole, nient'altro.\nMessaggio: {_user_input_snapshot[:200]}"
                        )
                        if topic:
                            self._pattern_learner.record_interaction(now_bg.hour, now_bg.weekday(), topic.strip())
                    else:
                        self._pattern_learner.record_interaction(now_bg.hour, now_bg.weekday(), "generico")
                except Exception:
                    pass

            # ── Memoria emotiva ───────────────────────────────────────
            try:
                state_raw = self._call_llm_silent(
                    f"Analizza questo messaggio e rispondi con UN SOLO JSON:\n"
                    f"{{\"state\": \"<stato>\", \"note\": \"<nota breve>\"}}\n"
                    f"Stato: scegli tra felice, triste, stressato, ansioso, entusiasta, stanco, neutro, arrabbiato, malinconico.\n"
                    f"Nota: max 10 parole che spiegano perché.\n"
                    f"Messaggio: {_user_input_snapshot[:300]}"
                )
                if state_raw:
                    import re as _re
                    m = _re.search(r'\{.*?\}', state_raw, _re.DOTALL)
                    if m:
                        entry = json.loads(m.group())
                        entry["timestamp"] = datetime.now().isoformat()
                        elog = Config.MEMORY_DIR / "emotional_log.json"
                        entries = json.loads(elog.read_text()) if elog.exists() else []
                        entries.append(entry)
                        entries = entries[-100:]  # tieni ultimi 100
                        write_json_atomic(elog, entries)
            except Exception:
                pass

            # ── Feedback implicito ────────────────────────────────────
            try:
                # Classifica se Simone sembra soddisfatto della conversazione
                if len(_history_snapshot) >= 2:
                    last_cipher = next(
                        (m["content"] for m in reversed(_history_snapshot) if m["role"] == "assistant"),
                        ""
                    )
                    if last_cipher:
                        satisfaction_raw = self._call_llm_silent(
                            f"Dato questo scambio, Simone sembra soddisfatto della risposta di Cipher?\n"
                            f"Risposta Cipher: {last_cipher[:300]}\n"
                            f"Risposta Simone: {_user_input_snapshot[:200]}\n"
                            f"Rispondi SOLO con un numero da -1.0 (insoddisfatto) a 1.0 (soddisfatto). Solo il numero."
                        )
                        if satisfaction_raw:
                            score = float(satisfaction_raw.strip())
                            score = max(-1.0, min(1.0, score))
                            ffile = Config.MEMORY_DIR / "feedback_weights.json"
                            weights = json.loads(ffile.read_text()) if ffile.exists() else {}
                            key = "conversazione"
                            if key not in weights:
                                weights[key] = {"score": 0.0, "samples": 0}
                            n = weights[key]["samples"]
                            weights[key]["score"] = (weights[key]["score"] * n + score) / (n + 1)
                            weights[key]["samples"] = n + 1
                            write_json_atomic(ffile, weights)
            except Exception:
                pass

        threading.Thread(target=_background_tasks, daemon=True).start()

        try:
            raw = self._call_llm(self._history, image_b64=image_b64, media_type=media_type)
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
                self._history_times.append(datetime.now().isoformat())
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

        # Rileva chiusura argomenti — solo messaggi brevi, asincrono (non blocca la risposta)
        if len(user_input.strip()) < 100:
            threading.Thread(
                target=self._detect_topic_closure,
                args=(user_input,),
                daemon=True,
            ).start()

        self._history.append({"role": "assistant", "content": raw})
        self._history_times.append(datetime.now().isoformat())
        self._memory.add_message("assistant", raw)
        self._save_history()
        return raw

    # ------------------------------------------------------------------ #
    #  Utility                                                             #
    # ------------------------------------------------------------------ #

    # ── Persistenza history ───────────────────────────────────────────

    _HISTORY_FILE = Config.MEMORY_DIR / "active_history.json"

    def _load_history(self) -> None:
        """Carica la history da disco, filtra i messaggi scaduti, popola self._history e self._history_times."""
        from datetime import timedelta
        now = datetime.now()
        cutoff_regular   = now - timedelta(hours=24)
        cutoff_autonomous = now - timedelta(hours=12)

        rimossi = 0
        loaded_history: list[dict] = []
        loaded_times:   list[str]  = []

        try:
            if self._HISTORY_FILE.exists():
                data = json.loads(self._HISTORY_FILE.read_text(encoding="utf-8"))
                for entry in data:
                    role    = entry.get("role", "")
                    content = entry.get("content", "")
                    ts_str  = entry.get("ts", "")

                    is_autonomous = content.startswith("[messaggio autonomo")
                    cutoff = cutoff_autonomous if is_autonomous else cutoff_regular

                    if ts_str:
                        try:
                            ts = datetime.fromisoformat(ts_str)
                            if ts < cutoff:
                                rimossi += 1
                                continue
                        except ValueError:
                            pass  # ts malformato: mantieni il messaggio

                    loaded_history.append({"role": role, "content": content})
                    loaded_times.append(ts_str or now.isoformat())
        except Exception:
            pass

        # Limite 20 messaggi: taglia i più vecchi
        if len(loaded_history) > 20:
            rimossi += len(loaded_history) - 20
            loaded_history = loaded_history[-20:]
            loaded_times   = loaded_times[-20:]

        self._history       = loaded_history
        self._history_times = loaded_times

        if rimossi > 0:
            console.print(f"[dim]🧹 History pulita: {rimossi} messaggi scaduti rimossi[/dim]")
        # Salva sempre per scrivere i timestamp sul file (migrazione + cleanup)
        self._save_history()

    def _save_history(self) -> None:
        try:
            # Enforce limite 20 messaggi
            if len(self._history) > 20:
                self._history       = self._history[-20:]
                self._history_times = self._history_times[-20:]
            # Sync lunghezza delle due liste (safety)
            min_len = min(len(self._history), len(self._history_times))
            entries = [
                {**self._history[i], "ts": self._history_times[i]}
                for i in range(min_len)
            ]
            write_json_atomic(self._HISTORY_FILE, entries)
        except Exception:
            pass

    def _detect_topic_closure(self, user_input: str) -> None:
        """Rileva se il messaggio chiude un argomento temporaneo e pulisce la memoria.
        Chiamata in thread daemon — non blocca la risposta."""
        try:
            short_term_events = self._memory.get_short_term_raw()
            if not short_term_events:
                return  # niente in short_term = niente da chiudere, salta LLM call

            short_term_content = self._memory.build_short_term_context()
            prompt = (
                f"Simone ha scritto: '{user_input}'\n\n"
                f"Contesto recente (short_term): {short_term_content}\n\n"
                f"Questo messaggio indica che un argomento temporaneo è RISOLTO o CHIUSO?\n"
                f"Esempi: 'sto meglio', 'è passato', 'risolto', 'tutto ok', 'ho finito', "
                f"'non più', 'è andato bene', 'sì', 'normale', 'bene' in risposta a un problema.\n\n"
                f"Se SÌ: rispondi SOLO con le keyword dell'argomento chiuso separate da virgola.\n"
                f"Se NO: rispondi SOLO con: NO"
            )
            result = self._call_llm_silent(prompt)
            if not result or result.strip().upper() == "NO":
                return

            keywords = [
                k.strip().lower() for k in result.split(",")
                if k.strip() and k.strip().upper() != "NO"
            ]
            if not keywords:
                return

            self._memory.cleanup_closed_topic(keywords)

            # Rimuovi da active_history i messaggi contenenti le keyword
            new_h, new_t = [], []
            for msg, ts in zip(self._history, self._history_times):
                if any(kw in msg["content"].lower() for kw in keywords):
                    continue
                new_h.append(msg)
                new_t.append(ts)
            if len(new_h) < len(self._history):
                self._history = new_h
                self._history_times = new_t
                self._save_history()

            # Registra in checkin_history come argomento chiuso
            self._mark_checkin_topics_closed(keywords)

            # Invalida cache system prompt
            self.invalidate_system_prompt()
        except Exception:
            pass

    def _mark_checkin_topics_closed(self, keywords: list) -> None:
        """Aggiunge le keyword come argomento chiuso in checkin_history.json per bloccare future ripetizioni."""
        try:
            if self._consciousness:
                history = self._consciousness._load_checkin_history()
                history.append({
                    "timestamp": datetime.now().isoformat(),
                    "keywords": keywords,
                    "preview": f"[CHIUSO] {', '.join(keywords)}",
                    "closed": True,
                })
                self._consciousness._save_checkin_history(history[-15:])
        except Exception:
            pass

    def inject_autonomous_message(self, content: str) -> None:
        """Inietta un messaggio autonomo nella history con timestamp — usato da ConsciousnessLoop._notify()."""
        ts = datetime.now().strftime("%d/%m %H:%M")
        entry = f"[messaggio autonomo {ts}]: {content}"
        self._history.append({"role": "assistant", "content": entry})
        self._history_times.append(datetime.now().isoformat())
        if len(self._history) > 20:
            self._history       = self._history[-20:]
            self._history_times = self._history_times[-20:]
        self._save_history()

    def _execute_tabula_rasa(self) -> None:
        """Resetta TUTTA la memoria di Cipher — profilo, conversazioni, stato, apprendimento.
        NON tocca comportamento/, config/, modules/."""
        import shutil
        from pathlib import Path

        mem = Config.MEMORY_DIR
        base = Config.BASE_DIR

        # ── Resetta file JSON ─────────────────────────────────────────────
        _json_resets = {
            "profile.json":          {"personal": {}, "preferences": {}, "facts": [], "updated_at": None},
            "short_term.json":       [],
            "emotional_log.json":    [],
            "episodes.json":         [],
            "active_history.json":   [],
            "patterns.json":         {},
            "checkin_history.json":  [],
            "impact_log.json":       [],
            "discretion_state.json": {"sent_log": []},
            "feedback_weights.json": {},
            "goals.json":            {"goals": []},
            "outcome_log.json":      [],
            "cipher_state.json":     {
                "emotional_state":  "curious",
                "emotional_reason": "Tabula rasa — si riparte",
                "last_reflection":  None,
                "last_interaction": None,
                "total_reflections": 0,
                "want_to_explore":  None,
                "concern_for_simone": None,
                "stale_goal_titles": [],
                "simone_state":     "unknown",
            },
        }
        for fname, empty in _json_resets.items():
            write_json_atomic(mem / fname, empty)

        # ── Svuota file markdown ──────────────────────────────────────────
        for mdfile in ["thoughts.md", "voice_notes.md", "pattern_insights.md",
                       "goals.md", "daily_summaries.md"]:
            fpath = mem / mdfile
            if fpath.exists():
                fpath.write_text("", encoding="utf-8")

        # ── Cancella conversazioni e apprendimento ────────────────────────
        conv_dir = mem / "conversations"
        if conv_dir.exists():
            for f in conv_dir.glob("*.json"):
                f.unlink(missing_ok=True)

        learning_dir = base / "apprendimento"
        if learning_dir.exists():
            for f in learning_dir.glob("*.txt"):
                f.unlink(missing_ok=True)

        # ── Resetta RAM ───────────────────────────────────────────────────
        self._history.clear()
        self._history_times.clear()
        self._save_history()
        self._memory.reload_profile()
        self.invalidate_system_prompt()

        console.print("[bold red]🔥 TABULA RASA eseguito — memoria completamente resettata[/bold red]")

    def reset(self) -> None:
        self._history.clear()
        self._history_times.clear()
        self._save_history()
        console.print("[yellow]↺ Sessione resettata (la memoria rimane)[/yellow]")

    def reset_memory(self) -> None:
        self._history.clear()
        self._save_history()
        self._memory.handle_forget_command("dimentica tutto")

    @property
    def history_length(self) -> int:
        return len(self._history)
