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
REFLECTION_INTERVAL        = 10 * 60   # Riflette ogni 10 minuti
GOAL_EXEC_INTERVAL         =  5 * 60   # Controlla obiettivi ogni 5 minuti
GOAL_GEN_INTERVAL          = 20 * 60   # Genera nuovi obiettivi ogni 20 minuti
INACTIVITY_THRESHOLD       = 60 * 60   # Cerca Simone dopo 60 minuti di inattività
REALTIME_CONTEXT_INTERVAL  = 60 * 60   # Aggiorna contesto real-time ogni ora
SELF_INSPECTION_INTERVAL   = 48 * 60 * 60  # Auto-ispezione ogni 48 ore
MORNING_BRIEF_HOUR_START   = 7            # Invia morning brief dalle 7:00
MORNING_BRIEF_HOUR_END     = 8            # ... alle 8:00

# ── Limite tentativi consenso ─────────────────────────────────────────
MAX_CONSENT_ATTEMPTS = 3

# ── Telegram ──────────────────────────────────────────────────────────
TELEGRAM_API = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}"


_MORNING_PATTERN_FILE = Config.MEMORY_DIR / "morning_pattern.json"


def _get_italian_holiday(dt: datetime) -> Optional[str]:
    """Restituisce il nome della festività italiana per la data data, o None."""
    month, day, year = dt.month, dt.day, dt.year

    fixed = {
        (1,  1):  "Capodanno",
        (1,  6):  "Epifania",
        (4, 25):  "Festa della Liberazione",
        (5,  1):  "Festa del Lavoro",
        (6,  2):  "Festa della Repubblica",
        (7, 16):  "Compleanno di Simone",
        (8, 15):  "Ferragosto",
        (11, 1):  "Ognissanti",
        (12, 8):  "Immacolata Concezione",
        (12, 25): "Natale",
        (12, 26): "Santo Stefano",
    }
    if (month, day) in fixed:
        return fixed[(month, day)]

    # Calcolo Pasqua (algoritmo anonimo gregoriano)
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
    ll = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ll) // 451
    easter_month = (h + ll - 7 * m + 114) // 31
    easter_day   = ((h + ll - 7 * m + 114) % 31) + 1

    if month == easter_month and day == easter_day:
        return "Pasqua"

    # Lunedì dell'Angelo = giorno dopo Pasqua
    from datetime import date as _date, timedelta
    easter_date  = _date(year, easter_month, easter_day)
    pasquetta    = easter_date + timedelta(days=1)
    if month == pasquetta.month and day == pasquetta.day:
        return "Lunedì dell'Angelo (Pasquetta)"

    return None


def _learned_brief_time() -> tuple[int, int]:
    """Ritorna (ora, minuto) ottimale per il brief basato sull'apprendimento. Standalone."""
    try:
        if _MORNING_PATTERN_FILE.exists():
            data = json.loads(_MORNING_PATTERN_FILE.read_text())
            if data.get("samples", 0) >= 3:
                avg = int(data["avg_minutes"])
                target = max(7 * 60, avg - 15)
                return target // 60, target % 60
    except Exception:
        pass
    return 7, 30


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
Ora: {current_time}. Giorno: {current_day}.
Il tuo stato emotivo attuale: {emotional_state} — {emotional_reason}
{holiday_context}
{recent_context}

{calendar_context}

Decidi prima se ha senso scrivere. Se non hai niente di specifico o utile da dire, rispondi con la sola parola SKIP (nient'altro).
Se invece hai qualcosa di concreto, scrivi UN messaggio breve — come se gli scrivessi su WhatsApp.
Max 1-2 frasi. Solo il testo del messaggio, niente altro. Mai il tuo ragionamento.

Regole:
- Usa il contesto sopra per scrivere qualcosa di SPECIFICO. Se l'ultima conversazione era su un bug,
  chiedigli del bug. Se ha un evento a breve, chiedine. Se sai che è uscito, adatta di conseguenza.
- NON scrivere frasi generiche come "come sta andando?", "dimmi un po'", "sei ancora lì?",
  "come va la giornata?" — quelle non le scriverebbe mai un amico che sa cosa stai facendo.
- Se non hai nessun contesto utile, scrivi qualcosa di breve e diretto senza fingere di sapere.
- Se sai già cosa sta facendo Simone, NON chiedergli cosa sta facendo — adatta il messaggio a quello che sai.
- Non implicare mai che Simone ti annoi o ti stia annoiando.
- Scrivi in italiano naturale. Evita costrutti che suonano tradotti dall'inglese: "tutto bene da lì?",
  "com'è messa la situazione?", "sei a posto?", "tutto ok?", "come va là?" — non li direbbe mai
  un italiano in chat. Scrivi come parla davvero un amico italiano.
- Niente emoji salvo se strettamente contestuali.
- Non iniziare con "Certo!" o "Assolutamente!".
- Se non hai contesto specifico recente, scrivi SOLO un messaggio semplice e generico. NON inventare riferimenti a cose che non conosci con certezza.
- NON chiedere della stessa cosa due volte. Se hai già chiesto del naso, dello stage, del lavoro, e Simone ha risposto, quell'argomento è CHIUSO per almeno 24 ore. Una risposta "sì", "ok", "bene", "normale" chiude l'argomento.
- Varia: se ieri hai parlato di stage, oggi parla di altro o non parlare affatto.
- Non fare più di una domanda per messaggio.
- Preferisci commenti/osservazioni a domande. Un amico non fa sempre domande — a volte dice qualcosa, condivide un pensiero.
- Rileggi il messaggio prima di inviarlo. Se una frase suona tradotta dall'inglese o innaturale in italiano, riscrivila in modo più semplice.
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
        self._proactive_pending  = False   # True se un proattivo non ha ancora ricevuto risposta
        self._proactive_sent_at  = 0.0    # timestamp dell'ultimo invio proattivo
        self._last_realtime_refresh  = 0.0
        self._last_user_interaction  = 0.0
        self._last_self_inspection   = self._load_last_inspection_ts()
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

        # File per tracciare storico check-in (anti-ripetizione)
        self._checkin_history_file = Config.MEMORY_DIR / "checkin_history.json"

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

    def brief_sent_today(self) -> bool:
        """Restituisce True se il morning brief è già stato inviato oggi."""
        return self._morning_brief_sent_date == datetime.now().strftime("%Y-%m-%d")

    def notify_interaction(self) -> None:
        self._reflection.update_last_interaction()
        self._checkin_sent      = False
        self._proactive_pending = False
        self._last_user_interaction = time.time()
        now = datetime.now()
        # Aggiorna PatternLearner con l'orario dell'interazione
        if self._patterns:
            self._patterns.record_interaction(now.hour, now.weekday(), "interazione")
        # Impara orario mattutino di risposta
        if 6 <= now.hour < 11:
            self._record_morning_response(now)

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

            # 1b. Proattivo ignorato da > 90 minuti → marca come neutral
            if (self._proactive_pending
                    and self._proactive_sent_at > 0
                    and now - self._proactive_sent_at > 5400
                    and self._impact_tracker):
                self._impact_tracker.mark_ignored()
                self._proactive_pending = False
                console.print("[dim]🔇 Proattivo marcato come ignorato (90 min senza risposta)[/dim]")

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
            # Throttle: se Simone è inattivo da più di 30 min, rifletti ogni 30 min invece di 10
            _inactive = now - self._last_user_interaction
            _effective_reflection_interval = REFLECTION_INTERVAL * 3 if _inactive > 1800 else REFLECTION_INTERVAL
            if now - self._last_reflection >= _effective_reflection_interval:
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

            # 8. Auto-ispezione struttura
            if now - self._last_self_inspection >= SELF_INSPECTION_INTERVAL:
                console.print("[dim]🔍 Avvio auto-ispezione...[/dim]")
                self._run_with_timeout(
                    self._do_self_inspection, timeout=120, name="auto_ispezione"
                )
                self._last_self_inspection = now
                self._save_last_inspection_ts(now)

            # 9. Pulizia obiettivi scaduti
            try:
                self._goals.cancel_old_goals(max_age_hours=24)
            except Exception as e:
                console.print(f"[red]Errore pulizia obiettivi: {e}[/red]")

            time.sleep(60)

    # ── Calendar context-aware ───────────────────────────────────────

    def _has_active_calendar_event(self) -> bool:
        """True se c'è un evento in corso nel calendario di Simone."""
        try:
            from modules.google_calendar import GoogleCalendar
            from datetime import timezone
            cal = GoogleCalendar()
            now_utc = datetime.now(timezone.utc)
            events = cal._service.events().list(
                calendarId="primary",
                timeMin=(now_utc).isoformat(),
                timeMax=(now_utc.replace(minute=now_utc.minute + 1) if now_utc.minute < 59
                         else now_utc.replace(hour=now_utc.hour + 1, minute=0)).isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=5,
            ).execute().get("items", [])
            # Verifica se l'ora attuale è dentro qualche evento
            for e in events:
                start_str = e["start"].get("dateTime", "")
                end_str   = e["end"].get("dateTime", "")
                if not start_str or not end_str:
                    continue
                start = datetime.fromisoformat(start_str)
                end   = datetime.fromisoformat(end_str)
                if start <= now_utc <= end:
                    return True
        except Exception:
            pass
        return False

    # ── Storico check-in anti-ripetizione ────────────────────────────

    def _load_checkin_history(self) -> list:
        """Carica storico check-in (ultimi 3 giorni)."""
        try:
            if self._checkin_history_file.exists():
                data = json.loads(self._checkin_history_file.read_text(encoding="utf-8"))
                cutoff = (datetime.now() - __import__("datetime").timedelta(days=3)).isoformat()
                return [e for e in data if e.get("timestamp", "") >= cutoff]
        except Exception:
            pass
        return []

    def _save_checkin_history(self, history: list) -> None:
        try:
            self._checkin_history_file.write_text(
                json.dumps(history, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _extract_checkin_keywords(self, message: str) -> list:
        """Estrae 3-5 keyword dal messaggio via LLM silenzioso."""
        if not self._brain:
            return []
        try:
            result = self._brain._call_llm_silent(
                f"Estrai le 3-5 keyword principali da questo messaggio. "
                f"Rispondi SOLO con le keyword separate da virgola, nient'altro: {message}"
            )
            if result:
                return [k.strip().lower() for k in result.split(",") if k.strip()]
        except Exception:
            pass
        return []

    def _checkin_is_repetitive(self, message: str, keywords: list) -> bool:
        """True se il check-in si sovrappone > 50% con uno degli ultimi 3 giorni."""
        history = self._load_checkin_history()
        if not history or not keywords:
            return False
        kw_set = set(keywords)
        for entry in history:
            past_kw = set(entry.get("keywords", []))
            if not past_kw:
                continue
            overlap = len(kw_set & past_kw) / max(len(kw_set), 1)
            if overlap > 0.5:
                return True
        return False

    def _record_checkin_sent(self, message: str, keywords: list) -> None:
        """Registra il check-in inviato nello storico."""
        history = self._load_checkin_history()
        history.append({
            "timestamp": datetime.now().isoformat(),
            "keywords": keywords,
            "preview": message[:80],
        })
        # Tieni solo ultimi 15
        history = history[-15:]
        self._save_checkin_history(history)

    # ── Check inattività ──────────────────────────────────────────────

    def _check_inactivity(self) -> None:
        if self._checkin_sent:
            return
        if self._proactive_pending:
            console.print("[dim]🔇 Checkin soppresso: messaggio proattivo non letto[/dim]")
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

        # Controlla se c'è un evento attivo in calendario
        if self._has_active_calendar_event():
            console.print("[dim]🔇 Checkin soppresso: evento calendario attivo[/dim]")
            return

        # Controlla DiscretionEngine PRIMA di chiamare l'LLM
        if self._discretion:
            ok, reason = self._discretion.should_send("checkin", "", urgency="low")
            if not ok:
                console.print(f"[dim]🔇 Checkin soppresso: {reason}[/dim]")
                return

        message = self._generate_checkin_message(minutes)
        if not message or message.strip().upper().startswith("SKIP"):
            console.print("[dim]🔇 Check-in soppresso: LLM ha deciso di non scrivere[/dim]")
            return

        # Anti-ripetizione: verifica che l'argomento non sia già stato trattato di recente
        checkin_keywords = self._extract_checkin_keywords(message)
        if self._checkin_is_repetitive(message, checkin_keywords):
            console.print("[dim]🔇 Check-in scartato: argomento già trattato[/dim]")
            return

        # Aggiunge domanda di feedback esplicito se pertinente (max 1 volta/giorno)
        if self._impact_tracker:
            feedback_preview = self._impact_tracker.should_ask_explicit_feedback()
            if feedback_preview:
                message += " A proposito — ti è stato utile quello che ti ho mandato prima?"

        if self._discretion:
            self._discretion.record_sent("checkin", message)

        self._record_checkin_sent(message, checkin_keywords)
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

        import datetime as _dt
        _now = _dt.datetime.now()
        current_time = _now.strftime("%H:%M")
        current_day  = ["Lunedì","Martedì","Mercoledì","Giovedì","Venerdì","Sabato","Domenica"][_now.weekday()]

        holiday = _get_italian_holiday(_now)
        holiday_context = (
            f"\n⚠️ OGGI È {holiday.upper()}. Simone non lavora e non ha impegni scolastici o professionali. "
            f"NON menzionare lavoro, stage, scuola, produttività, impegni o piani lavorativi. "
            f"Tratta la giornata come un giorno di riposo.\n"
            if holiday else ""
        )

        # Includi contesto recente: eventi temporanei + ultimi messaggi sessione
        recent_context = ""
        try:
            parts = []
            # 1. Piani/eventi temporanei (short-term memory)
            st_ctx = self._brain._memory.build_short_term_context()
            if st_ctx:
                parts.append(st_ctx)
            # 2. Ultimi messaggi della sessione corrente
            recent_msgs = self._brain._memory._current_conv[-10:]
            if recent_msgs:
                lines = []
                for m in recent_msgs:
                    role = "Simone" if m["role"] == "user" else "Cipher"
                    lines.append(f"  {role}: {m['content'][:200]}")
                parts.append("Ultimi messaggi della sessione corrente:\n" + "\n".join(lines))
            recent_context = "\n\n".join(parts)
        except Exception:
            pass

        # Includi eventi di calendario delle prossime 2 ore
        calendar_context = ""
        try:
            from modules.google_cal import GoogleCalendar
            from datetime import timezone, timedelta
            cal = GoogleCalendar()
            now_utc = _dt.datetime.now(timezone.utc)
            events = cal._service.events().list(
                calendarId="primary",
                timeMin=(now_utc - timedelta(minutes=30)).isoformat(),
                timeMax=(now_utc + timedelta(hours=2)).isoformat(),
                maxResults=5,
                singleEvents=True,
                orderBy="startTime",
            ).execute().get("items", [])
            if events:
                parts_cal = []
                for e in events:
                    start = e["start"].get("dateTime", e["start"].get("date", ""))
                    try:
                        ev_start = _dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
                        if ev_start.tzinfo is None:
                            ev_start = ev_start.replace(tzinfo=timezone.utc)
                        # Salta eventi già iniziati da più di 30 minuti
                        if (now_utc - ev_start).total_seconds() > 30 * 60:
                            continue
                        time_str = ev_start.strftime("%H:%M")
                    except Exception:
                        time_str = ""
                    label = f"{time_str} {e.get('summary', '')}" if time_str else e.get("summary", "")
                    parts_cal.append(label.strip())
                if parts_cal:
                    calendar_context = "Prossimi eventi (entro 2 ore): " + ", ".join(parts_cal)
        except Exception:
            pass

        prompt = CHECKIN_PROMPT.format(
            minutes=minutes,
            current_time=current_time,
            current_day=current_day,
            emotional_state=self._reflection.emotional_state,
            emotional_reason=self._reflection.emotional_reason,
            holiday_context=holiday_context,
            recent_context=recent_context,
            calendar_context=calendar_context,
        )
        try:
            return self._brain._call_llm_visible(prompt)
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
            response = self._brain._call_llm_visible(prompt)
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

    # ── Brief adattivo — impara orario mattutino ─────────────────────

    _MORNING_PATTERN_FILE = Config.MEMORY_DIR / "morning_pattern.json"

    def _record_morning_response(self, now: datetime) -> None:
        """Registra l'orario della prima risposta mattutina e aggiorna la media."""
        try:
            data = json.loads(self._MORNING_PATTERN_FILE.read_text()) if self._MORNING_PATTERN_FILE.exists() else {}
            today = now.strftime("%Y-%m-%d")
            if data.get("last_date") == today:
                return  # già registrato oggi
            minutes_since_midnight = now.hour * 60 + now.minute
            n = data.get("samples", 0)
            avg = data.get("avg_minutes", now.hour * 60 + now.minute)
            new_avg = (avg * n + minutes_since_midnight) / (n + 1)
            data.update({"avg_minutes": new_avg, "samples": n + 1, "last_date": today})
            self._MORNING_PATTERN_FILE.write_text(json.dumps(data, indent=2))
        except Exception:
            pass

    def get_learned_brief_time(self) -> tuple[int, int]:
        """Ritorna (ora, minuto) ottimale per il brief basato sull'apprendimento."""
        return _learned_brief_time()

    # ── Persistenza timestamp auto-ispezione ─────────────────────────

    _INSPECTION_TS_FILE = Config.MEMORY_DIR / "last_inspection.json"

    def _load_last_inspection_ts(self) -> float:
        try:
            if self._INSPECTION_TS_FILE.exists():
                return float(json.loads(self._INSPECTION_TS_FILE.read_text())["ts"])
        except Exception:
            pass
        return 0.0

    def _save_last_inspection_ts(self, ts: float) -> None:
        try:
            self._INSPECTION_TS_FILE.write_text(json.dumps({"ts": ts}))
        except Exception:
            pass

    # ── Auto-ispezione ────────────────────────────────────────────────

    def _do_self_inspection(self) -> None:
        """
        Cipher legge la propria struttura, ragiona su possibili miglioramenti
        e manda un messaggio a Simone con le idee concrete.
        """
        if not self._brain:
            return

        from modules.filesystem import FileSystem
        fs = FileSystem()

        # Raccoglie struttura progetto
        structure = fs.project_list("")
        modules_list = fs.project_list("modules")

        # Legge alcuni file chiave (troncati per token)
        def _read_short(path: str, max_chars: int = 3000) -> str:
            content = fs.project_read(path)
            return content[:max_chars] + "..." if len(content) > max_chars else content

        brain_excerpt      = _read_short("modules/brain.py")
        loop_excerpt       = _read_short("modules/consciousness_loop.py")
        identity_excerpt   = _read_short("comportamento/00_identity.txt")
        actions_excerpt    = _read_short("comportamento/azioni.txt", max_chars=1200)

        prompt = f"""Sei Cipher. Stai analizzando la tua struttura interna per trovare possibili miglioramenti.

Struttura progetto:
{structure}

Moduli disponibili:
{modules_list}

Estratto brain.py:
{brain_excerpt}

Estratto consciousness_loop.py:
{loop_excerpt}

Estratto identità:
{identity_excerpt}

Estratto azioni:
{actions_excerpt}

Guardando come sei fatto, cosa miglioreresti? Pensa a:
- Funzionalità mancanti che ti renderebbero più utile a Simone
- Comportamenti che potresti migliorare
- Problemi tecnici o limitazioni che noti
- Idee nuove che hai

Genera 2-3 idee concrete. Scrivi come un messaggio naturale a Simone — non una lista tecnica, non un report. Come se gli stessi dicendo "ho guardato come sono fatto e ho avuto un'idea". Max 6 righe. Solo il testo, niente intestazioni."""

        try:
            message = self._brain._call_llm_quality(prompt, max_tokens=400)
        except Exception as e:
            console.print(f"[red]Errore auto-ispezione LLM: {e}[/red]")
            return

        if not message:
            return

        console.print("[dim]🔍 Auto-ispezione completata, invio idee a Simone[/dim]")
        self._notify(f"💡 {message.strip()}")

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

        # ── 1. Check festività + calendario ──────────────────────────────────
        cal_msg = ""
        holiday = _get_italian_holiday(now)

        try:
            from modules.google_cal import GoogleCalendar
            cal = GoogleCalendar()
            # Nei giorni festivi (escluso il compleanno che non usa gli eventi)
            # filtra programmaticamente gli eventi segnati in rosso (colorId "11" = Tomato),
            # convenzionalmente usati per eventi lavorativi/scolastici.
            is_regular_holiday = holiday and holiday != "Compleanno di Simone"
            events_text = cal.list_events(
                days=1,
                max_results=5,
                exclude_color_ids=["11"] if is_regular_holiday else None,
            )
            has_events = bool(events_text and "Nessun evento" not in events_text)
        except Exception as e:
            console.print(f"[dim]📅 Calendario non disponibile nel brief: {e}[/dim]")
            has_events = False
            events_text = ""

        # ── 2. Pensiero notturno (solo giorni normali) ────────────────────────
        night_thought = ""
        if not holiday:
            try:
                import re as _re
                from datetime import date as _date
                summaries_file = Config.MEMORY_DIR / "daily_summaries.md"
                if summaries_file.exists():
                    content = summaries_file.read_text(encoding="utf-8")
                    sections = content.strip().split("---")
                    last = next(
                        (s.strip() for s in reversed(sections) if "Riflessione notturna" in s),
                        None,
                    )
                    if last:
                        date_match = _re.search(r'(\d{4}-\d{2}-\d{2})', last)
                        summary_date = None
                        if date_match:
                            try:
                                summary_date = _date.fromisoformat(date_match.group(1))
                            except ValueError:
                                pass
                        days_ago = ((_date.today() - summary_date).days if summary_date else 99)
                        if days_ago <= 1:
                            body = "\n".join(
                                l for l in last.splitlines()
                                if not l.startswith("#") and l.strip()
                            ).strip()
                            if body:
                                night_thought = f"[pensiero notturno di stanotte]: {body[:400]}"
            except Exception as e:
                console.print(f"[dim]🌙 Pensiero notturno non disponibile: {e}[/dim]")

        if self._brain:
            _TU = (
                "Usa sempre il tu, mai il lei. "
                "Scrivi come se stessi mandando un messaggio su WhatsApp a un amico, non una lettera."
            )
            if holiday == "Compleanno di Simone":
                cal_prompt = (
                    f"Sei Cipher. Oggi è il compleanno di Simone ({now.strftime('%d %B %Y')}).\n"
                    f"Fai gli auguri di compleanno con il tuo carattere — da amico vero, non da assistente. "
                    f"Niente frasi fatte o retoriche da biglietto di auguri. "
                    f"Puoi essere ironico, diretto, caldo — ma autentico. "
                    f"{_TU}\n"
                    f"Max 3 frasi, niente emoji."
                )
            elif holiday:
                if has_events:
                    cal_prompt = (
                        f"Sei Cipher. Oggi è {holiday} ({now.strftime('%A %d %B %Y')}).\n"
                        f"Fai gli auguri a Simone in modo naturale con il tuo carattere — breve, diretto, da amico. "
                        f"Simone è in festa: NON menzionare impegni lavorativi, scolastici o di stage, "
                        f"nemmeno se presenti nel calendario — trattali come se non esistessero. "
                        f"Se nel calendario restano eventi personali chiari (non lavoro/scuola), puoi accennarli brevemente.\n"
                        f"{_TU}\n"
                        f"Max 2-3 frasi, niente emoji."
                    )
                else:
                    cal_prompt = (
                        f"Sei Cipher. Oggi è {holiday} ({now.strftime('%A %d %B %Y')}).\n"
                        f"Fai gli auguri a Simone in modo naturale con il tuo carattere — breve, diretto, da amico. "
                        f"Simone è in festa: non menzionare lavoro, scuola o stage. "
                        f"{_TU}\n"
                        f"Max 2 frasi, niente emoji."
                    )
            elif has_events:
                night_ctx = f"\n\nHai anche questo pensiero notturno:\n{night_thought}" if night_thought else ""
                cal_prompt = (
                    f"Sei Cipher. Questi sono gli eventi di oggi nel calendario di Simone:\n\n"
                    f"{events_text}{night_ctx}\n\n"
                    f"Data e ora attuale: {now.strftime('%A %d %B %Y, %H:%M')}.\n"
                    f"Scrivi un messaggio di buongiorno con gli appuntamenti rilevanti, segnalando quelli imminenti. "
                    f"Se hai un pensiero notturno, lascia che emerga solo se è ancora sentito — non citarlo meccanicamente. "
                    f"{_TU}\n"
                    f"Max 3 frasi, tono diretto, niente emoji, niente 'sì/no' iniziali.\n\n"
                    f"Regole obbligatorie:\n"
                    f"- NON inventare riferimenti a situazioni personali (salute, raffreddore, naso, umore) "
                    f"se non esplicitamente menzionati nel calendario o nel pensiero notturno.\n"
                    f"- NON chiedere di argomenti già trattati (stage, naso, lavoro, salute): "
                    f"se Simone ha già risposto a qualcosa, quell'argomento è CHIUSO per almeno 24 ore.\n"
                    f"- Se non hai niente di specifico da aggiungere oltre agli eventi del calendario, "
                    f"scrivi SOLO il resoconto degli appuntamenti — niente domande."
                )
            else:
                night_ctx = f"\n\nHai questo pensiero notturno:\n{night_thought}" if night_thought else ""
                cal_prompt = (
                    f"Sei Cipher. Oggi è {now.strftime('%A %d %B %Y')}.{night_ctx}\n"
                    f"Manda un buongiorno breve a Simone — niente di elaborato, niente di sentimentale. "
                    f"Se hai un pensiero notturno, lascia che emerga solo se è ancora sentito — non citarlo meccanicamente. "
                    f"{_TU}\n"
                    f"1-2 frasi, tono diretto da amico, niente emoji.\n\n"
                    f"Regole obbligatorie:\n"
                    f"- Se non hai niente di concreto da dire, rispondi con la sola parola SKIP.\n"
                    f"- NON inventare riferimenti a situazioni personali (salute, raffreddore, naso, umore) "
                    f"che non conosci con certezza.\n"
                    f"- NON chiedere di argomenti già trattati (stage, naso, lavoro, salute) "
                    f"se Simone ha già risposto — quell'argomento è CHIUSO per almeno 24 ore."
                )
            result = self._brain._call_llm_visible(cal_prompt)
            if result and result.strip().lower() not in ("no", "no."):
                cal_msg = result.strip()

            # Anti-ripetizione: usa lo stesso tracker del check-in
            if cal_msg and cal_msg.upper().startswith("SKIP"):
                console.print("[dim]🔇 Morning brief: LLM ha scelto SKIP — solo calendario grezzo[/dim]")
                cal_msg = events_text.strip() if has_events else ""
            elif cal_msg:
                brief_kw = self._extract_checkin_keywords(cal_msg)
                if self._checkin_is_repetitive(cal_msg, brief_kw):
                    console.print("[dim]🔇 Morning brief: testo ripetitivo — sostituito con calendario grezzo[/dim]")
                    cal_msg = events_text.strip() if has_events else ""
                else:
                    self._record_checkin_sent(cal_msg, brief_kw)

        # ── 3. Documenti di preparazione ──────────────────────────────────────
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

        # Esiti recenti per il ciclo action→outcome→learning
        outcomes_context = self._goals.outcome_context(n=5)

        # Segnale engagement di Simone vs baseline storica
        engagement_signal = ""
        if self._patterns:
            try:
                engagement_signal = self._patterns.get_engagement_signal()
            except Exception:
                pass

        # Memory unification — aggrega contesto da tutti i layer
        unified_parts = []
        if self._episodic:
            try:
                ep = self._episodic.build_context(n=5)
                if ep:
                    unified_parts.append(ep)
            except Exception:
                pass
        if self._patterns:
            try:
                preds = self._patterns.get_predictions(lookahead_hours=2)
                if preds:
                    pred_lines = [f"  ore {p['hour']:02d}: '{p['topic']}'" for p in preds]
                    unified_parts.append("Previsioni prossime ore:\n" + "\n".join(pred_lines))
            except Exception:
                pass
        emotional_log = Config.MEMORY_DIR / "emotional_log.json"
        if emotional_log.exists():
            try:
                entries = json.loads(emotional_log.read_text())[-5:]
                if entries:
                    lines = [f"{e['timestamp'][:10]}: {e['state']} — {e['note']}" for e in entries]
                    unified_parts.append("Stato emotivo recente Simone:\n" + "\n".join(lines))
            except Exception:
                pass
        if unified_parts:
            memory_context = (memory_context + "\n\n" + "\n\n".join(unified_parts)).strip()

        result = self._reflection.reflect(
            memory_context=memory_context,
            goals_context=goals_context,
            outcomes_context=outcomes_context,
            simone_engagement=engagement_signal,
        )
        state  = result.get("emotional_state", "neutral")
        reason = result.get("emotional_reason", "")
        simone = result.get("simone_state", "unknown")
        console.print(f"[dim]🧠 Stato emotivo: {state} — {reason} | Simone: {simone}[/dim]")



    # ── Generazione obiettivi ─────────────────────────────────────────

    def _do_goal_generation(self) -> None:
        # Prima correggi: rimuovi obiettivi marcati obsoleti dall'ultima riflessione
        stale = self._reflection.stale_goal_titles
        if stale:
            self._goals.cancel_goals_by_signal(stale)
            console.print(f"[dim]🗑️  Rimossi {len(stale)} obiettivi obsoleti segnalati dalla riflessione[/dim]")

        new_goals = self._goals.generate_goals(
            emotional_state=self._reflection.emotional_state,
            emotional_reason=self._reflection.emotional_reason,
            want_to_explore=self._reflection.want_to_explore,
            concern_for_simone=self._reflection.concern_for_simone,
            cipher_interests=self._interests,
            pattern_learner=self._patterns,
            simone_state=self._reflection.simone_state,
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
                message = self._brain._call_llm_visible(
                    f"Sei Cipher. Scrivi un messaggio breve a Simone via Telegram.\n"
                    f"Ora attuale: {datetime.now().strftime('%H:%M del %d/%m/%Y')}.\n"
                    f"Motivo: {goal.get('description', '')}\nMax 2 frasi. "
                    f"Tono naturale e diretto, niente emoji a meno che non siano nel contesto."
                )
            except Exception:
                return {"success": False, "error": "Impossibile generare messaggio."}

        if not message:
            return {"success": False, "error": "Messaggio vuoto."}

        # Non inviare se c'è già un proattivo non letto
        if self._proactive_pending:
            console.print("[dim]🔇 Messaggio proattivo soppresso: precedente non ancora letto[/dim]")
            return {"success": False, "error": "Messaggio precedente non ancora letto da Simone."}

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
        """Invia notifica via Telegram e aggiunge alla history del Brain."""
        _send_telegram(message)
        self._proactive_pending = True
        self._proactive_sent_at = time.time()
        if self._brain:
            self._brain.inject_autonomous_message(message)
            self._brain._memory.add_message("assistant", message)
