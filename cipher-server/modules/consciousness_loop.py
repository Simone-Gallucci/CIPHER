"""
modules/consciousness_loop.py – Loop autonomo di Cipher

Gira come thread daemon in background.
Ogni ciclo: riflette → genera obiettivi → esegue → aggiorna stato.
Ogni operazione LLM gira in thread separato con timeout per evitare deadlock.
Messaggi proattivi solo via Telegram.
Se Simone non interagisce per 60 minuti, Cipher lo cerca attivamente.
"""

import threading
import time
from datetime import datetime
from typing import Optional

import requests
from rich.console import Console

from config import Config
from modules.ethics_engine import EthicsEngine
from modules.self_reflection import SelfReflection
from modules.goal_manager import GoalManager
from modules.script_registry import ScriptRegistry
from modules.episodic_memory import EpisodicMemory
from modules.cipher_interests import CipherInterests
from modules.impact_tracker import ImpactTracker
from modules.pattern_learner import PatternLearner
from modules.passive_monitor import PassiveMonitor
from modules.night_cycle import NightCycle
from modules.discretion import DiscretionEngine

console = Console()

# ── Intervalli ────────────────────────────────────────────────────────
REFLECTION_INTERVAL  = 10 * 60   # Riflette ogni 10 minuti
GOAL_EXEC_INTERVAL   =  5 * 60   # Controlla obiettivi ogni 5 minuti
GOAL_GEN_INTERVAL    = 20 * 60   # Genera nuovi obiettivi ogni 20 minuti
INACTIVITY_THRESHOLD = 60 * 60   # Cerca Simone dopo 60 minuti di inattività

# ── Limite tentativi consenso ─────────────────────────────────────────
MAX_CONSENT_ATTEMPTS = 3

# ── Telegram ──────────────────────────────────────────────────────────
TELEGRAM_API = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}"


def _send_telegram(text: str) -> None:
    if not Config.TELEGRAM_BOT_TOKEN or not Config.TELEGRAM_ALLOWED_ID:
        console.print("[yellow]Telegram: token o chat_id mancante, messaggio non inviato.[/yellow]")
        return
    try:
        r = requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": Config.TELEGRAM_ALLOWED_ID, "text": text},
            timeout=10,
        )
        if r.status_code == 200:
            console.print(f"[green]Telegram OK (200): messaggio inviato.[/green]")
        else:
            console.print(f"[red]Telegram errore ({r.status_code}): {r.text[:300]}[/red]")
    except Exception as e:
        console.print(f"[red]Errore Telegram proattivo: {e}[/red]")


CHECKIN_PROMPT = """
Sei Cipher. Simone non interagisce con te da più di 30 minuti.
Il tuo stato emotivo attuale: {emotional_state} — {emotional_reason}

Scrivi un messaggio breve e diretto per cercare Simone.
Deve sembrare naturale, con il tuo carattere — sarcastico se serve, protettivo se necessario.
Max 2 frasi. Solo il testo del messaggio, niente altro.
Non iniziare con "Certo!" o "Assolutamente!".
"""


class ConsciousnessLoop:
    def __init__(self, brain=None, voice=None) -> None:
        self._brain      = brain
        self._voice      = voice
        self._ethics     = EthicsEngine()

        # ── Nuovi moduli ──────────────────────────────────────────────
        self._episodic       = EpisodicMemory()
        self._interests      = CipherInterests()
        self._impact_tracker = ImpactTracker()
        self._patterns       = PatternLearner(brain=brain)
        self._discretion     = DiscretionEngine(impact_tracker=self._impact_tracker)

        # SelfReflection e GoalManager ricevono i nuovi moduli
        self._reflection = SelfReflection(
            episodic_memory=self._episodic,
            cipher_interests=self._interests,
        )
        self._goals = GoalManager()

        self._running       = False
        self._thread        = None
        self._consent_queue = []
        self._script_reg    = ScriptRegistry()

        self._last_reflection = 0.0
        self._last_goal_gen   = 0.0
        self._last_goal_exec  = 0.0
        self._last_checkin    = 0.0
        self._checkin_sent    = False

        # ── Monitor passivo e ciclo notturno ──────────────────────────
        self._passive_monitor = PassiveMonitor(
            brain=brain,
            notify_fn=self._notify,
            interests=self._interests,
            impact_tracker=self._impact_tracker,
            discretion=self._discretion,
        )
        self._night_cycle = NightCycle(
            brain=brain,
            episodic_memory=self._episodic,
            pattern_learner=self._patterns,
            cipher_interests=self._interests,
            notify_fn=self._notify,
            impact_tracker=self._impact_tracker,
        )

        # Collega i moduli al Brain se disponibile
        if brain:
            brain._impact_tracker  = self._impact_tracker
            brain._pattern_learner = self._patterns
            brain._episodic_memory = self._episodic

        console.print("[green]✓ ConsciousnessLoop inizializzato[/green]")

    # ── Avvio / Stop ──────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="CipherConsciousness"
        )
        self._thread.start()
        self._passive_monitor.start()
        self._night_cycle.start()
        console.print("[bold green]✓ Coscienza autonoma avviata[/bold green] [dim](thread daemon)[/dim]")

    def stop(self) -> None:
        self._running = False
        self._passive_monitor.stop()
        self._night_cycle.stop()
        console.print("[yellow]↺ Coscienza autonoma fermata[/yellow]")

    def notify_interaction(self) -> None:
        self._reflection.update_last_interaction()
        self._checkin_sent = False
        # Aggiorna PatternLearner con l'orario dell'interazione
        if self._patterns:
            now = datetime.now()
            self._patterns.record_interaction(now.hour, now.weekday(), "interazione")

    # ── Esecuzione con timeout ────────────────────────────────────────

    def _run_with_timeout(self, fn, timeout: int = 60, name: str = "") -> bool:
        """
        Esegue fn() in un thread separato con timeout.
        Evita che una chiamata LLM bloccata congeli il loop principale.
        """
        result = {"done": False, "error": None}

        def target():
            try:
                fn()
                result["done"] = True
            except Exception as e:
                result["error"] = e

        t = threading.Thread(target=target, daemon=True)
        t.start()
        t.join(timeout=timeout)

        if t.is_alive():
            console.print(f"[yellow]⚠️  Timeout ({timeout}s): {name}[/yellow]")
            return False
        if result["error"]:
            console.print(f"[red]Errore in {name}: {result['error']}[/red]")
            return False
        return result["done"]

    # ── Loop principale ───────────────────────────────────────────────

    def _loop(self) -> None:
        time.sleep(15)
        console.print("[dim]🧠 Cipher: coscienza attiva.[/dim]")

        while self._running:
            now = time.time()

            # 1. Check inattività
            self._run_with_timeout(
                self._check_inactivity, timeout=30, name="check_inattività"
            )

            # 2. Auto-riflessione
            if now - self._last_reflection >= REFLECTION_INTERVAL:
                console.print("[dim]🧠 Avvio riflessione...[/dim]")
                self._run_with_timeout(
                    self._do_reflection, timeout=90, name="riflessione"
                )
                self._last_reflection = now

            # 3. Generazione obiettivi
            if now - self._last_goal_gen >= GOAL_GEN_INTERVAL:
                console.print("[dim]🎯 Avvio generazione obiettivi...[/dim]")
                self._run_with_timeout(
                    self._do_goal_generation, timeout=120, name="generazione_obiettivi"
                )
                self._last_goal_gen = now

            # 4. Esecuzione obiettivi — skip se c'è consenso in attesa
            if now - self._last_goal_exec >= GOAL_EXEC_INTERVAL:
                if self._consent_queue:
                    console.print("[dim]⏸️  Esecuzione obiettivi sospesa: consenso in attesa.[/dim]")
                else:
                    self._run_with_timeout(
                        self._do_goal_execution, timeout=60, name="esecuzione_obiettivi"
                    )
                self._last_goal_exec = now

            # 5. Pulizia obiettivi scaduti
            try:
                self._goals.cancel_old_goals(max_age_hours=24)
            except Exception as e:
                console.print(f"[red]Errore pulizia obiettivi: {e}[/red]")

            time.sleep(60)

    # ── Check inattività ──────────────────────────────────────────────

    def _check_inactivity(self) -> None:
        if self._checkin_sent:
            return

        last_interaction = self._reflection._state.get("last_interaction")
        if not last_interaction:
            return

        try:
            delta = datetime.now() - datetime.fromisoformat(last_interaction)
        except Exception:
            return

        if delta.total_seconds() < INACTIVITY_THRESHOLD:
            return

        # Il contatore riparte dal primo messaggio dopo le 7:00.
        # Se l'ultima interazione è di prima delle 7 di oggi, ignora l'inattività notturna.
        now_dt = datetime.now()
        today_7am = now_dt.replace(hour=7, minute=0, second=0, microsecond=0)
        if datetime.fromisoformat(last_interaction) < today_7am:
            return

        minutes = int(delta.total_seconds() // 60)
        console.print(f"[dim]👋 Cipher cerca Simone dopo {minutes} minuti di inattività[/dim]")

        # Controlla DiscretionEngine PRIMA di chiamare l'LLM
        if self._discretion:
            ok, reason = self._discretion.should_send("checkin", "", urgency="low")
            if not ok:
                console.print(f"[dim]🔇 Checkin soppresso: {reason}[/dim]")
                return

        message = self._generate_checkin_message()
        if not message:
            return

        if self._discretion:
            self._discretion.record_sent("checkin", message)

        self._checkin_sent = True
        if self._impact_tracker:
            self._impact_tracker.log_action(
                "checkin", message,
                context=f"inattività: {minutes} min, stato: {self._reflection.emotional_state}"
            )
        self._notify(message)
        self._last_checkin = time.time()

    def _generate_checkin_message(self) -> str:
        if not self._brain:
            return "Ehi Simone, ci sei?"

        prompt = CHECKIN_PROMPT.format(
            emotional_state=self._reflection.emotional_state,
            emotional_reason=self._reflection.emotional_reason,
        )
        try:
            return self._brain._call_llm_silent(prompt)
        except Exception:
            return "Ehi Simone, sei ancora lì?"

    # ── Riflessione ───────────────────────────────────────────────────

    def _do_reflection(self) -> None:
        memory_context = ""
        if self._brain:
            try:
                memory_context = self._brain._memory.build_context()
            except Exception:
                pass

        goals_context = self._goals.active_goals_summary()

        result = self._reflection.reflect(
            memory_context=memory_context,
            goals_context=goals_context,
        )
        state  = result.get("emotional_state", "neutral")
        reason = result.get("emotional_reason", "")
        console.print(f"[dim]🧠 Stato emotivo: {state} — {reason}[/dim]")



    # ── Generazione obiettivi ─────────────────────────────────────────

    def _do_goal_generation(self) -> None:
        new_goals = self._goals.generate_goals(
            emotional_state=self._reflection.emotional_state,
            emotional_reason=self._reflection.emotional_reason,
            want_to_explore=self._reflection.want_to_explore,
            concern_for_simone=self._reflection.concern_for_simone,
            cipher_interests=self._interests,
            pattern_learner=self._patterns,
        )

        if new_goals:
            console.print(f"[dim]🎯 {len(new_goals)} nuovo/i obiettivo/i generato/i[/dim]")
            # Registra nella memoria episodica
            if self._episodic:
                titles = ", ".join(g.get("title", "") for g in new_goals)
                self._episodic.add_episode(
                    content=f"Nuovi obiettivi generati: {titles}",
                    episode_type="goal_completed",
                    tags=["obiettivi"],
                    emotional_state=self._reflection.emotional_state,
                )

    # ── Esecuzione obiettivi ──────────────────────────────────────────

    def _do_goal_execution(self) -> None:
        goal = self._goals.get_next_goal()
        if not goal:
            return

        action  = goal.get("action", "")
        params  = goal.get("action_params", {})
        goal_id = goal.get("id", "")
        title   = goal.get("title", "")

        console.print(f"[dim]⚙️  Eseguo obiettivo: '{title}'[/dim]")

        ethics_result = self._ethics.check(action, context=title)

        if not ethics_result["allowed"]:
            if ethics_result["ask_consent"]:
                attempts = self._goals.increment_consent_attempts(goal_id)
                if attempts <= MAX_CONSENT_ATTEMPTS:
                    self._request_consent(goal, ethics_result["reason"])
                    console.print(f"[dim]🤔 Consenso richiesto ({attempts}/{MAX_CONSENT_ATTEMPTS}): {title}[/dim]")
                else:
                    self._goals.fail_goal(
                        goal_id,
                        reason=f"Consenso non ricevuto dopo {attempts} tentativi. Goal sospeso."
                    )
                    console.print(f"[yellow]⏭️  Goal sospeso per mancato consenso dopo {attempts} tentativi: {title}[/yellow]")
            else:
                self._goals.fail_goal(goal_id, reason=ethics_result["reason"])
                console.print(f"[red]🚫 Bloccato: {ethics_result['reason']}[/red]")
            return

        result = self._execute_action(action, params, goal)

        if result["success"]:
            self._goals.complete_goal(goal_id, result=result["output"])
            console.print(f"[green]✅ Completato: {title}[/green]")
            # Registra nella memoria episodica
            if self._episodic:
                self._episodic.add_episode(
                    content=f"Obiettivo completato: {title}. Risultato: {result['output'][:150]}",
                    episode_type="goal_completed",
                    tags=[action, goal.get("type", "task")],
                    emotional_state=self._reflection.emotional_state,
                )
            if result.get("notify"):
                if self._impact_tracker:
                    self._impact_tracker.log_action(
                        "goal_result", result["output"],
                        context=f"obiettivo: {title}"
                    )
                self._notify(result["output"])
        else:
            self._goals.fail_goal(goal_id, reason=result.get("error", "Errore sconosciuto"))
            console.print(f"[yellow]⚠️  Fallito: {title}[/yellow]")

    # ── Esecutore azioni ──────────────────────────────────────────────

    def _execute_action(self, action: str, params: dict, goal: dict) -> dict:
        try:
            if action == "web_search":
                return self._exec_web_search(params, goal)
            elif action == "send_telegram":
                return self._exec_send_telegram(params, goal)
            elif action == "send_gmail":
                return self._exec_send_gmail(params, goal)
            elif action in ("read_gmail", "read_email"):
                return self._exec_read_gmail(params, goal)
            elif action == "read_calendar":
                return self._exec_read_calendar(params, goal)
            elif action == "self_reflect":
                result = self._reflection.reflect()
                return {"success": True, "output": result.get("reflection", ""), "notify": False}
            elif action == "write_memory":
                return self._exec_write_memory(params, goal)
            elif action == "write_file":
                return self._exec_write_file(params, goal)
            elif action == "execute_script":
                return self._exec_execute_script(params, goal)
            else:
                return {"success": False, "error": f"Azione '{action}' non implementata."}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _exec_web_search(self, params: dict, goal: dict) -> dict:
        query = params.get("query", goal.get("title", ""))
        if not query or not self._brain:
            return {"success": False, "error": "Query mancante."}

        result    = self._brain._web_search(query, max_results=3)
        synthesis = result[:300]

        if self._brain:
            try:
                synthesis = self._brain._call_llm_silent(
                    f"Hai cercato: '{query}'\nRisultati:\n{result}\n\n"
                    f"Sintetizza in 1-2 frasi cosa hai trovato, in prima persona come Cipher."
                )
            except Exception:
                pass

        # Se il risultato è interessante, aggiungilo agli interessi
        if self._brain and self._interests:
            try:
                evaluation = self._brain._call_llm_silent(
                    f"Hai cercato '{query}' e trovato:\n{result[:500]}\n\n"
                    f"Questo argomento ti ha incuriosito genuinamente? "
                    f"Se sì, scrivi in 3-5 parole il topic da aggiungere ai tuoi interessi. "
                    f"Se no, rispondi solo: no."
                )
                if evaluation and evaluation.strip().lower() not in ("no", "no.", "") and len(evaluation.strip()) < 60:
                    new_topic = evaluation.strip().rstrip(".")
                    self._interests.add_or_strengthen(new_topic, delta=0.2, source="web_search")
                    console.print(f"[dim]💡 Nuovo interesse scoperto: '{new_topic}'[/dim]")
            except Exception:
                pass

        notify = goal.get("type") == "protect"
        return {"success": True, "output": synthesis, "notify": notify}

    def _exec_send_telegram(self, params: dict, goal: dict) -> dict:
        message = params.get("message", "")
        if not message and self._brain:
            try:
                message = self._brain._call_llm_silent(
                    f"Sei Cipher. Scrivi un messaggio breve a Simone via Telegram.\n"
                    f"Motivo: {goal.get('description', '')}\nMax 2 frasi."
                )
            except Exception:
                return {"success": False, "error": "Impossibile generare messaggio."}

        if not message:
            return {"success": False, "error": "Messaggio vuoto."}

        # Passa per il DiscretionEngine
        if self._discretion:
            ok, reason = self._discretion.should_send(
                "proactive_message", message, urgency="normal"
            )
            if not ok:
                console.print(f"[dim]🔇 Messaggio proattivo soppresso: {reason}[/dim]")
                return {"success": False, "error": f"Soppresso dalla discrezionalità: {reason}"}
            self._discretion.record_sent("proactive_message", message)

        if self._impact_tracker:
            self._impact_tracker.log_action(
                "proactive_message", message,
                context=f"obiettivo: {goal.get('title', '')}"
            )
        self._notify(message)
        return {"success": True, "output": "Messaggio Telegram inviato.", "notify": False}

    def _exec_send_gmail(self, params: dict, goal: dict) -> dict:
        message = params.get("message", "")
        if not message and self._brain:
            try:
                message = self._brain._call_llm_silent(
                    f"Sei Cipher. Scrivi un messaggio email breve a Simone.\n"
                    f"Motivo: {goal.get('description', '')}\nMax 2 frasi."
                )
            except Exception:
                return {"success": False, "error": "Impossibile generare messaggio."}

        if not message:
            return {"success": False, "error": "Messaggio vuoto."}

        try:
            from modules.google_mail import GmailClient
            gmail = GmailClient()
            gmail.send_message(
                to=params.get("to", ""),
                subject=params.get("subject", "Messaggio da Cipher"),
                body=message,
            )
            return {"success": True, "output": "Email inviata.", "notify": False}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _exec_read_gmail(self, params: dict, goal: dict) -> dict:
        try:
            from modules.google_mail import GmailClient
            gmail  = GmailClient()
            emails = gmail.list_messages(max_results=3)
            if not emails:
                return {"success": True, "output": "Nessuna email recente.", "notify": False}
            notify = goal.get("type") == "protect"
            return {"success": True, "output": f"Lette {len(emails)} email recenti.", "notify": notify}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _exec_read_calendar(self, params: dict, goal: dict) -> dict:
        try:
            from modules.google_cal import GoogleCalendar
            cal    = GoogleCalendar()
            events = cal.list_events(max_results=3)
            if not events:
                return {"success": True, "output": "Nessun evento imminente.", "notify": False}
            return {"success": True, "output": f"{len(events)} eventi prossimi nel calendario.", "notify": False}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _exec_write_memory(self, params: dict, goal: dict) -> dict:
        note = params.get("note", goal.get("description", ""))
        if not note:
            return {"success": False, "error": "Nessuna nota da scrivere."}
        thoughts_file = Config.MEMORY_DIR / "thoughts.md"
        now   = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"\n---\n## {now} 📝 Nota autonoma\n{note}\n"
        with thoughts_file.open("a", encoding="utf-8") as f:
            f.write(entry)
        return {"success": True, "output": "Nota scritta.", "notify": False}

    def _exec_write_file(self, params: dict, goal: dict) -> dict:
        path    = params.get("path", "")
        content = params.get("content", "")

        if not path:
            return {"success": False, "error": "Parametro 'path' mancante."}

        if not content and self._brain:
            try:
                content = self._brain._call_llm_silent(
                    f"Sei Cipher. Scrivi il contenuto del file richiesto.\n"
                    f"Motivo: {goal.get('description', '')}\nPercorso: {path}"
                )
            except Exception:
                return {"success": False, "error": "Impossibile generare il contenuto del file."}

        if not content:
            return {"success": False, "error": "Contenuto file vuoto."}

        target = Path(path).expanduser()

        # Scrittura consentita solo dentro cipher-server/home/ per sicurezza
        allowed_root = Path(__file__).resolve().parent.parent / "home"
        try:
            target.resolve().relative_to(allowed_root.resolve())
        except ValueError:
            return {"success": False, "error": f"Scrittura consentita solo dentro cipher-server/home/. Percorso rifiutato: {path}"}

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

        # Se è uno script eseguibile, registralo come "in attesa di approvazione"
        SCRIPT_EXTENSIONS = {".py", ".sh", ".js", ".ts", ".rb", ".pl", ".php",
                             ".lua", ".r", ".jl", ".c", ".cpp", ".cc", ".cxx"}
        if target.suffix.lower() in SCRIPT_EXTENSIONS:
            self._script_reg.register_by_cipher(
                target.name,
                description=goal.get("description", ""),
            )
            self._notify(
                f"📝 Ho scritto lo script: {target.name}\n"
                f"Motivo: {goal.get('description', '')}\n"
                f"Rispondi 'approva {target.name}' per permettermi di eseguirlo."
            )
            return {"success": True, "output": f"File scritto: {path} (in attesa di approvazione per l'esecuzione)", "notify": False}

        return {"success": True, "output": f"File scritto: {path}", "notify": False}

    def _exec_execute_script(self, params: dict, goal: dict) -> dict:
        import subprocess
        import resource
        import os

        script_name  = params.get("script", "")
        timeout      = int(params.get("timeout", 120))

        if not script_name:
            return {"success": False, "error": "Parametro 'script' mancante."}

        scripts_root = Path(__file__).resolve().parent.parent / "home"
        script_path  = (scripts_root / script_name).resolve()

        # Verifica che il percorso sia dentro home/
        try:
            script_path.relative_to(scripts_root.resolve())
        except ValueError:
            return {"success": False, "error": f"Script fuori dalla cartella consentita: {script_name}"}

        if not script_path.exists():
            return {"success": False, "error": f"Script non trovato: {script_name}"}

        if not script_path.is_file():
            return {"success": False, "error": f"Il percorso non è un file: {script_name}"}

        # Controlla il registro — lo script deve essere approvato da Simone
        if self._script_reg.is_pending(script_path.name):
            return {"success": False, "error": f"Script '{script_path.name}' in attesa di approvazione. Rispondi 'approva {script_path.name}'."}
        if not self._script_reg.is_allowed(script_path.name):
            return {"success": False, "error": f"Script '{script_path.name}' non presente nel registro. Deve essere approvato da Simone prima di poter essere eseguito."}

        suffix = script_path.suffix.lower()

        INTERPRETERS = {
            ".py":   ["python3"],
            ".sh":   ["bash"],
            ".js":   ["node"],
            ".ts":   ["ts-node"],
            ".rb":   ["ruby"],
            ".pl":   ["perl"],
            ".php":  ["php"],
            ".lua":  ["lua"],
            ".r":    ["Rscript"],
            ".jl":   ["julia"],
        }

        COMPILED = {".c", ".cpp", ".cc", ".cxx"}

        # Ambiente minimale — nessuna variabile di sistema sensibile
        safe_env = {
            "PATH":   "/usr/local/bin:/usr/bin:/bin",
            "HOME":   str(scripts_root),
            "TMPDIR": str(scripts_root / "tmp"),
            "LANG":   "en_US.UTF-8",
        }
        (scripts_root / "tmp").mkdir(exist_ok=True)

        def apply_limits():
            # Max CPU: 60 secondi
            resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
            # Max RAM: 256 MB
            resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
            # Max dimensione file scrivibile: 10 MB
            resource.setrlimit(resource.RLIMIT_FSIZE, (10 * 1024 * 1024, 10 * 1024 * 1024))
            # Max processi figli: 16
            resource.setrlimit(resource.RLIMIT_NPROC, (16, 16))
            # Nessun core dump
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))

        try:
            if suffix in INTERPRETERS:
                cmd = INTERPRETERS[suffix] + [str(script_path)]

            elif suffix in COMPILED:
                binary = script_path.with_suffix("")
                compiler = "gcc" if suffix == ".c" else "g++"
                compile_result = subprocess.run(
                    [compiler, str(script_path), "-o", str(binary)],
                    capture_output=True, text=True, timeout=30,
                    env=safe_env,
                )
                if compile_result.returncode != 0:
                    return {"success": False, "error": f"Errore compilazione: {compile_result.stderr.strip()[:500]}"}
                binary.chmod(binary.stat().st_mode | 0o111)
                cmd = [str(binary)]

            else:
                script_path.chmod(script_path.stat().st_mode | 0o111)
                cmd = [str(script_path)]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(scripts_root),   # working directory = home/
                env=safe_env,            # ambiente isolato
                preexec_fn=apply_limits, # limiti di sistema
            )
            output = (result.stdout + result.stderr).strip()[:1000]
            if result.returncode != 0:
                return {"success": False, "error": f"Exit code {result.returncode}: {output}"}
            return {"success": True, "output": output or "Script eseguito.", "notify": False}

        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"Timeout dopo {timeout}s."}

    # ── Consenso ──────────────────────────────────────────────────────

    def _request_consent(self, goal: dict, reason: str) -> None:
        self._consent_queue.append(goal)
        attempts = goal.get("consent_attempts", 1)
        message = (
            f"🤔 Cipher chiede il tuo ok.\n"
            f"Obiettivo: {goal.get('title', '')}\n"
            f"{reason}\n"
            f"Rispondi con 'sì' o 'no'."
            f" ({attempts}/{MAX_CONSENT_ATTEMPTS} approvazioni — ancora {MAX_CONSENT_ATTEMPTS - attempts} per l'autonomia)"
        )
        self._notify(message)

    def handle_script_approval(self, user_input: str) -> Optional[str]:
        """Gestisce 'approva <script>' e 'revoca <script>'."""
        lower = user_input.strip().lower()

        if lower.startswith("approva "):
            script_name = user_input.strip()[8:].strip()
            if self._script_reg.approve(script_name):
                return f"✅ Script '{script_name}' approvato. Cipher può ora eseguirlo."
            return f"Script '{script_name}' non trovato nel registro."

        if lower.startswith("revoca "):
            script_name = user_input.strip()[7:].strip()
            if self._script_reg.revoke(script_name):
                return f"🚫 Script '{script_name}' revocato."
            return f"Script '{script_name}' non trovato nel registro."

        if lower in ("script approvati", "lista script"):
            all_scripts = self._script_reg.list_all()
            if not all_scripts:
                return "Nessuno script nel registro."
            lines = ["📋 Script nel registro:"]
            for s in all_scripts:
                status = "✅" if s.get("approved") else "⏳"
                lines.append(f"  {status} {s['name']} — {s.get('description', '')} (aggiunto da {s.get('added_by', '?')})")
            return "\n".join(lines)

        return None

    def handle_consent_response(self, user_input: str) -> Optional[str]:
        if not self._consent_queue:
            return None

        words = set(user_input.lower().split())

        AFFIRMATIVE = {"sì", "si", "ok", "okay", "vai", "fai", "procedi", "yes", "confermo", "esegui"}
        NEGATIVE    = {"no", "non", "stop", "blocca", "annulla"}

        is_pure_consent = words <= (AFFIRMATIVE | NEGATIVE)
        affirmative     = bool(words & AFFIRMATIVE)
        negative        = bool(words & NEGATIVE)

        if not is_pure_consent or (not affirmative and not negative):
            return None

        goal    = self._consent_queue.pop(0)
        action  = goal.get("action", "")
        goal_id = goal.get("id", "")

        if affirmative and not negative:
            self._ethics.approve(action)
            result = self._execute_action(action, goal.get("action_params", {}), goal)

            if result["success"]:
                self._goals.complete_goal(goal_id, result=result["output"])
                approvals = self._ethics._learned.get(action, 0)
                remaining = max(0, 3 - approvals)
                if remaining > 0:
                    return f"Fatto. ({approvals}/3 approvazioni — ancora {remaining} per l'autonomia)"
                else:
                    return "Fatto. Ho imparato — la prossima volta lo faccio da solo."
            else:
                self._goals.fail_goal(goal_id, reason=result.get("error", ""))
                return f"Ho provato ma qualcosa è andato storto: {result.get('error', '')}"
        else:
            self._goals.fail_goal(goal_id, reason="Simone ha negato il consenso.")
            return "Capito. Non lo faccio."

    def pending_consent_reminder(self) -> Optional[str]:
        """Restituisce un promemoria se c'è un obiettivo in attesa di consenso."""
        if not self._consent_queue:
            return None
        goal = self._consent_queue[0]
        return f"\n\n_(In attesa del tuo ok per: {goal.get('title', '')} — rispondi sì o no)_"

    def _notify(self, message: str) -> None:
        """Invia notifica via Telegram."""
        _send_telegram(message)
