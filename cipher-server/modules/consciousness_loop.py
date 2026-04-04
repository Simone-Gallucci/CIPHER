"""
modules/consciousness_loop.py – Loop autonomo di Cipher

Gira come thread daemon in background.
Ogni ciclo: riflette → genera obiettivi → esegue → aggiorna stato.
Ogni operazione LLM gira in thread separato con timeout per evitare deadlock.
Messaggi proattivi solo via Telegram.
Se Simone non interagisce per 60 minuti, Cipher lo cerca attivamente.
"""

import json
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
from modules.realtime_context import RealtimeContext

console = Console()

# ── Intervalli ────────────────────────────────────────────────────────
REFLECTION_INTERVAL   = 10 * 60   # Riflette ogni 10 minuti
GOAL_EXEC_INTERVAL    =  5 * 60   # Controlla obiettivi ogni 5 minuti
GOAL_GEN_INTERVAL     = 20 * 60   # Genera nuovi obiettivi ogni 20 minuti
INACTIVITY_THRESHOLD  = 60 * 60   # Cerca Simone dopo 60 minuti di inattività
REALTIME_CONTEXT_INTERVAL  = 60 * 60      # Aggiorna contesto real-time ogni ora
MORNING_BRIEF_HOUR_START   = 7            # Invia morning brief dalle 7:00
MORNING_BRIEF_HOUR_END     = 8            # ... alle 8:00

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
Sei Cipher. Simone non interagisce con te da {minutes} minuti.
Il tuo stato emotivo attuale: {emotional_state} — {emotional_reason}

Scrivi un messaggio breve per cercare Simone — come se gli scrivessi su WhatsApp.
Max 2 frasi. Solo il testo del messaggio, niente altro.

Regole:
- Non implicare mai che Simone ti annoi o ti stia annoiando — al massimo gli manca, o vuoi sapere cosa sta combinando.
- Varia l'apertura: non iniziare sempre con "dove sei finito" o simili.
- Scrivi in italiano corretto: usa "ad" davanti a parole che iniziano per vocale (es. "ad annoiarmi", "ad aspettare").
- Niente emoji salvo se strettamente contestuali.
- Non iniziare con "Certo!" o "Assolutamente!".
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
        self._realtime       = RealtimeContext(cipher_interests=self._interests)

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

        self._last_reflection    = 0.0
        self._last_goal_gen      = 0.0
        self._last_goal_exec     = 0.0
        self._last_checkin       = 0.0
        self._checkin_sent       = False
        self._last_realtime_refresh = 0.0
        self._last_user_interaction = 0.0
        self._morning_brief_sent_date = None  # data (str) dell'ultimo brief inviato

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
        self._last_user_interaction = time.time()
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

            # 2. Contesto real-time
            if now - self._last_realtime_refresh >= REALTIME_CONTEXT_INTERVAL:
                self._run_with_timeout(
                    self._realtime.refresh, timeout=20, name="realtime_context"
                )
                self._last_realtime_refresh = now

            # 4. Morning brief
            self._run_with_timeout(
                self._send_morning_brief, timeout=20, name="morning_brief"
            )

            # 5. Auto-riflessione
            if now - self._last_reflection >= REFLECTION_INTERVAL:
                console.print("[dim]🧠 Avvio riflessione...[/dim]")
                self._run_with_timeout(
                    self._do_reflection, timeout=90, name="riflessione"
                )
                self._last_reflection = now

            # 6. Generazione obiettivi
            if now - self._last_goal_gen >= GOAL_GEN_INTERVAL:
                console.print("[dim]🎯 Avvio generazione obiettivi...[/dim]")
                self._run_with_timeout(
                    self._do_goal_generation, timeout=120, name="generazione_obiettivi"
                )
                self._last_goal_gen = now

            # 7. Esecuzione obiettivi — sospesa solo se c'è consenso in attesa
            if now - self._last_goal_exec >= GOAL_EXEC_INTERVAL:
                if self._consent_queue:
                    console.print("[dim]⏸️  Esecuzione obiettivi sospesa: consenso in attesa.[/dim]")
                else:
                    self._run_with_timeout(
                        self._do_goal_execution, timeout=60, name="esecuzione_obiettivi"
                    )
                self._last_goal_exec = now

            # 8. Pulizia obiettivi scaduti
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

        message = self._generate_checkin_message(minutes)
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

    def _generate_checkin_message(self, minutes: int = 60) -> str:
        if not self._brain:
            return "Ehi Simone, ci sei?"

        prompt = CHECKIN_PROMPT.format(
            minutes=minutes,
            emotional_state=self._reflection.emotional_state,
            emotional_reason=self._reflection.emotional_reason,
        )
        try:
            return self._brain._call_llm_silent(prompt)
        except Exception:
            return "Ehi Simone, sei ancora lì?"

    # ── Festività italiane ────────────────────────────────────────────

    @staticmethod
    def _easter(year: int) -> datetime:
        """Calcola la data di Pasqua (algoritmo anonimo gregoriano)."""
        a = year % 19
        b = year // 100
        c = year % 100
        d = b // 4
        e = b % 4
        f = (b + 8) // 25
        g = (b - f + 1) // 3
        h = (19 * a + b - d - g + 15) % 30
        i = c // 4
        k = c % 4
        l = (32 + 2 * e + 2 * i - h - k) % 7
        m = (a + 11 * h + 22 * l) // 451
        month = (h + l - 7 * m + 114) // 31
        day   = ((h + l - 7 * m + 114) % 31) + 1
        return datetime(year, month, day)

    @staticmethod
    def _italian_holiday(date: datetime) -> str | None:
        """
        Ritorna il nome della festività se `date` è un giorno festivo italiano
        (festività nazionali + Venerdì Santo + Pasquetta), altrimenti None.
        """
        d, m, y = date.day, date.month, date.year
        fixed = {
            (1,  1):  "Capodanno",
            (6,  1):  "Epifania",
            (25, 4):  "Festa della Liberazione",
            (1,  5):  "Festa dei Lavoratori",
            (2,  6):  "Festa della Repubblica",
            (15, 8):  "Ferragosto",
            (1,  11): "Tutti i Santi",
            (8,  12): "Immacolata Concezione",
            (25, 12): "Natale",
            (26, 12): "Santo Stefano",
        }
        if (d, m) in fixed:
            return fixed[(d, m)]
        easter = ConsciousnessLoop._easter(y).replace(hour=0, minute=0, second=0, microsecond=0)
        check  = date.replace(hour=0, minute=0, second=0, microsecond=0)
        from datetime import timedelta
        if check == easter - timedelta(days=2):
            return "Venerdì Santo"
        if check == easter:
            return "Pasqua"
        if check == easter + timedelta(days=1):
            return "Pasquetta"
        return None

    # ── Proattività calendario ────────────────────────────────────────

    def _check_upcoming_events(self) -> None:
        """
        Controlla il calendario per le prossime 24 ore.
        Se trova eventi rilevanti, prepara Simone autonomamente.
        Non invia nulla di notte (22:00–7:00) salvo eventi urgenti.
        """
        now_hour = datetime.now().hour
        if 22 <= now_hour or now_hour < 7:
            return
        # Nella finestra mattutina il calendario è già incluso nel morning brief
        if MORNING_BRIEF_HOUR_START <= now_hour < MORNING_BRIEF_HOUR_END:
            return

        # Guard festività: niente reminder se oggi è festivo
        now_dt  = datetime.now()
        holiday = self._italian_holiday(now_dt)
        if holiday:
            console.print(f"[dim]📅 Calendar check saltato: oggi è {holiday}[/dim]")
            return

        try:
            from modules.google_cal import GoogleCalendar
            cal = GoogleCalendar()
            events_text = cal.list_events(days=1, max_results=5)
        except Exception as e:
            console.print(f"[dim]📅 Calendario non disponibile: {e}[/dim]")
            return

        if "Nessun evento" in events_text or not self._brain:
            return

        # Chiede all'LLM se c'è qualcosa di imminente (entro 1 ora) che richiede attenzione.
        # Finestra intenzionalmente breve per non duplicare il morning brief che copre già la giornata.
        # Gli eventi di domani vengono gestiti dal digest serale delle 20:00 — non duplicare.
        prompt = (
            f"Sei Cipher. Hai appena controllato il calendario di Simone:\n\n"
            f"{events_text}\n\n"
            f"Data e ora attuale: {now_dt.strftime('%A %d %B %Y, %H:%M')}.\n"
            f"C'è qualcosa che inizia entro i prossimi 60 minuti e richiede attenzione immediata? "
            f"Considera solo eventi di OGGI, non quelli di domani o giorni successivi. "
            f"Se sì, scrivi un messaggio breve (max 2 frasi, tono diretto, niente emoji). "
            f"Non iniziare mai la risposta con 'sì' o 'no' — vai diretto al punto. "
            f"Se no, rispondi solo: no."
        )
        try:
            response = self._brain._call_llm_silent(prompt)
        except Exception:
            return

        if not response or response.strip().lower() in ("no", "no.", ""):
            return

        # Passa per DiscretionEngine
        if self._discretion:
            ok, reason = self._discretion.should_send("calendar_reminder", response, urgency="normal")
            if not ok:
                console.print(f"[dim]🔇 Calendar reminder soppresso: {reason}[/dim]")
                return
            self._discretion.record_sent("calendar_reminder", response)

        console.print("[dim]📅 Cipher: notifica calendario proattiva[/dim]")
        if self._impact_tracker:
            self._impact_tracker.log_action(
                "calendar_reminder", response,
                context=f"eventi: {events_text[:100]}"
            )
        self._notify(response)

    # ── Morning brief ─────────────────────────────────────────────────

    def _send_morning_brief(self) -> None:
        """
        Tra le 7:00 e le 8:00, invia a Simone:
        - Il check del calendario (eventi di oggi imminenti)
        - I documenti di preparazione preparati stanotte
        Una volta sola al giorno.
        """
        now      = datetime.now()
        today    = now.strftime("%Y-%m-%d")

        # Solo nella finestra mattutina e non già inviato oggi
        if not (MORNING_BRIEF_HOUR_START <= now.hour < MORNING_BRIEF_HOUR_END):
            return
        if self._morning_brief_sent_date == today:
            return

        # ── 1. Check calendario ───────────────────────────────────────────────
        cal_msg = ""
        try:
            from modules.google_cal import GoogleCalendar
            cal = GoogleCalendar()
            events_text = cal.list_events(days=1, max_results=5)
            if events_text and "Nessun evento" not in events_text and self._brain:
                cal_prompt = (
                    f"Sei Cipher. Questi sono gli eventi di oggi nel calendario di Simone:\n\n"
                    f"{events_text}\n\n"
                    f"Data e ora attuale: {now.strftime('%A %d %B %Y, %H:%M')}.\n"
                    f"Se oggi è un giorno festivo (es. Venerdì Santo, Pasqua, Natale, Ferragosto, festività nazionale, ecc.), "
                    f"fai gli auguri in modo naturale con il tuo carattere — senza essere stucchevole — "
                    f"e NON segnalare impegni lavorativi o scolastici (Simone non lavora nei giorni festivi). "
                    f"Se invece è un giorno normale, scrivi un messaggio di buongiorno con gli appuntamenti rilevanti, "
                    f"segnalando quelli imminenti. "
                    f"Max 3 frasi, tono diretto, niente emoji, niente 'sì/no' iniziali."
                )
                result = self._brain._call_llm_silent(cal_prompt)
                if result and result.strip().lower() not in ("no", "no."):
                    cal_msg = result.strip()
        except Exception as e:
            console.print(f"[dim]📅 Calendario non disponibile nel brief: {e}[/dim]")

        # ── 2. Documenti di preparazione ──────────────────────────────────────
        # Il cal_msg viene preposto al primo documento per evitare due saluti separati.
        brief_file = Config.MEMORY_DIR / "morning_brief.json"
        docs_sent = False
        if brief_file.exists():
            try:
                brief = json.loads(brief_file.read_text(encoding="utf-8"))
                if brief.get("date") == today and not brief.get("sent"):
                    documents = brief.get("documents", [])
                    first = True
                    for doc in documents:
                        doc_path = Config.HOME_DIR / doc.get("doc_name", "")
                        if not doc_path.exists():
                            continue
                        content = doc_path.read_text(encoding="utf-8")
                        if first and cal_msg:
                            content = cal_msg + "\n\n" + content
                            first = False
                        message = content[:3900]
                        if len(content) > 3900:
                            message += "\n\n_(continua nel file)_"
                        self._notify(message)
                        docs_sent = True
                    brief["sent"] = True
                    brief_file.write_text(
                        json.dumps(brief, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    console.print(f"[green]📅 Morning brief inviato: {len(documents)} documento/i[/green]")
            except Exception:
                pass

        # Nessun documento: manda il cal_msg da solo
        if not docs_sent and cal_msg:
            self._notify(cal_msg)

        self._morning_brief_sent_date = today

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
                    f"Ora attuale: {datetime.now().strftime('%H:%M del %d/%m/%Y')}.\n"
                    f"Motivo: {goal.get('description', '')}\nMax 2 frasi. "
                    f"Tono naturale e diretto, niente emoji a meno che non siano nel contesto."
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
                    f"Ora attuale: {datetime.now().strftime('%H:%M del %d/%m/%Y')}.\n"
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
            f"Ti chiedo il permesso.\n"
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
