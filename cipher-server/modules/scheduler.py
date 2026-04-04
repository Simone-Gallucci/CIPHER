"""
modules/scheduler.py – Task automatici di Cipher
- Briefing mattutino alle 7:30
- Task personalizzati definiti via Cipher (voce/testo/Telegram)
  salvati in ~/cipher/scheduling/tasks.json
"""

import json
import logging
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

from config import Config

log = logging.getLogger("cipher.scheduler")

SCHEDULING_DIR  = Config.BASE_DIR / "scheduling"
TASKS_FILE      = SCHEDULING_DIR / "tasks.json"
TELEGRAM_API    = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}"
BRIEFING_HOUR   = 7
BRIEFING_MINUTE = 30
DIGEST_HOUR     = 20
DIGEST_MINUTE   = 0
CHECK_INTERVAL  = 60

GIORNI = ["Lunedì","Martedì","Mercoledì","Giovedì","Venerdì","Sabato","Domenica"]
MESI   = ["","Gennaio","Febbraio","Marzo","Aprile","Maggio","Giugno",
          "Luglio","Agosto","Settembre","Ottobre","Novembre","Dicembre"]


def _italian_date(dt: datetime) -> str:
    return f"{GIORNI[dt.weekday()]} {dt.day} {MESI[dt.month]} {dt.year}"


def _send_telegram(text: str) -> None:
    try:
        requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": Config.TELEGRAM_ALLOWED_ID, "text": text},
            timeout=10,
        )
    except Exception as e:
        log.error("Errore invio Telegram: %s", e)


class Scheduler:
    def __init__(self) -> None:
        SCHEDULING_DIR.mkdir(parents=True, exist_ok=True)
        self._stop          = threading.Event()
        self._briefing_sent = None
        self._digest_sent   = None
        self._briefing_lock = threading.Lock()
        self._digest_lock   = threading.Lock()
        self._calendar      = None
        self._brain         = None   # impostato da server.py
        self._tasks: list[dict] = self._load_tasks()

    # ── Persistenza task ──────────────────────────────────────────────────────

    def _load_tasks(self) -> list[dict]:
        if TASKS_FILE.exists():
            try:
                return json.loads(TASKS_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return []

    def _save_tasks(self) -> None:
        TASKS_FILE.write_text(
            json.dumps(self._tasks, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── Gestione task ─────────────────────────────────────────────────────────

    def add_task(self, task: dict) -> int:
        task_id = max((t["id"] for t in self._tasks), default=0) + 1
        task["id"]       = task_id
        task["last_run"] = None
        self._tasks.append(task)
        self._save_tasks()
        log.info("Task aggiunto: #%d %s", task_id, task.get("label", ""))
        return task_id

    def remove_task(self, task_id: int) -> bool:
        before = len(self._tasks)
        self._tasks = [t for t in self._tasks if t["id"] != task_id]
        if len(self._tasks) < before:
            self._save_tasks()
            return True
        return False

    def list_tasks(self) -> list[dict]:
        return list(self._tasks)

    # ── Servizi ───────────────────────────────────────────────────────────────

    def _get_calendar(self):
        if self._calendar is None:
            from modules.google_cal import GoogleCalendar
            self._calendar = GoogleCalendar()
        return self._calendar

    # ── Briefing mattutino ────────────────────────────────────────────────────

    def _send_briefing(self) -> None:
        import re as _re
        from datetime import date as _date, timedelta as _td

        now = datetime.now()

        # ── Raccoglie contesto: pensiero notturno ─────────────────────
        night_thought = ""
        try:
            summaries_file = Config.MEMORY_DIR / "daily_summaries.md"
            if summaries_file.exists():
                content  = summaries_file.read_text(encoding="utf-8")
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
                    days_ago = ((_date.today() - summary_date).days
                                if summary_date else 99)
                    if days_ago <= 2:
                        body = "\n".join(
                            l for l in last.splitlines()
                            if not l.startswith("#") and l.strip()
                        ).strip()
                        if body:
                            when = "ieri notte" if days_ago == 1 else f"notte di {summary_date.strftime('%A')}"
                            night_thought = f"[pensiero notturno di {when}]: {body[:400]}"
        except Exception as e:
            log.error("Briefing sommario notturno error: %s", e)

        # ── Raccoglie contesto: agenda di oggi ───────────────────────
        events_context = ""
        try:
            cal    = self._get_calendar()
            events = cal._service.events().list(
                calendarId="primary",
                timeMin=datetime.now(timezone.utc).isoformat(),
                timeMax=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                maxResults=8,
                singleEvents=True,
                orderBy="startTime",
            ).execute().get("items", [])

            if events:
                parts = []
                for e in events:
                    start = e["start"].get("dateTime", e["start"].get("date", ""))
                    try:
                        time_str = datetime.fromisoformat(start).strftime("%H:%M")
                    except Exception:
                        time_str = ""
                    label = f"{time_str} {e.get('summary', '')}" if time_str else e.get("summary", "")
                    parts.append(label.strip())
                events_context = "[agenda di oggi]: " + ", ".join(parts)
        except Exception as e:
            log.error("Briefing agenda error: %s", e)

        # ── Genera il messaggio via LLM ───────────────────────────────
        message = ""
        if self._brain:
            ctx_parts = [x for x in [night_thought, events_context] if x]
            ctx_block = "\n".join(ctx_parts)
            prompt = (
                f"Sei Cipher. Stai scrivendo il messaggio di buongiorno a Simone ({_italian_date(now)}).\n"
                + (f"\nHai questo contesto:\n{ctx_block}\n" if ctx_block else "")
                + "\nScrivi un messaggio breve e naturale. "
                "Inizia sempre con 'Buongiorno Simone,' — è il suo nome, usalo. "
                "Non una lista, non sezioni separate: un testo unico che fluisce. "
                "Se hai un pensiero notturno, non citarlo meccanicamente: lascia che emerga come parte "
                "di quello che hai in testa stamattina, solo se è ancora rilevante e sentito. "
                "Il focus deve essere su Simone, non su di te — non esprimere dubbi sulla tua risposta "
                "o insicurezze proprie: sii presente e caldo, non introspettivo su te stesso. "
                "Se c'è agenda, menzionala in modo discorsivo. Se non c'è agenda, non menzionare il calendario. "
                "Se oggi è una festa (Natale, Capodanno, Pasqua, compleanno, ecc.), fai gli auguri in modo naturale, integrato nel messaggio — non come formula a parte. "
                "Tono: diretto, caldo, amichevole. "
                "Max 4-5 righe. Solo il testo del messaggio, niente intestazioni o emoji strutturali. "
                "NON menzionare preparativi per domani o cosa fare stasera: quello lo dirai stasera."
            )
            try:
                message = self._brain._call_llm_silent(prompt)
            except Exception:
                pass

        if message:
            _send_telegram(f"☀️ {_italian_date(now)}\n\n{message.strip()}")
        else:
            fallback = [f"☀️ Buongiorno Simone! {_italian_date(now)}"]
            if events_context:
                fallback.append("\n📅 " + events_context.replace("[agenda di oggi]: ", ""))
            _send_telegram("\n".join(fallback))

        log.info("Briefing mattutino inviato.")

    def _send_evening_digest(self) -> None:
        """Digest serale alle 20:00."""
        lines = []
        has_content = False

        # ── Agenda di domani ──────────────────────────────────────────
        try:
            from datetime import date as _date, timedelta as _td
            tomorrow_start = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            tomorrow_end = tomorrow_start + timedelta(days=1)
            cal    = self._get_calendar()
            events = cal._service.events().list(
                calendarId="primary",
                timeMin=tomorrow_start.isoformat(),
                timeMax=tomorrow_end.isoformat(),
                maxResults=8,
                singleEvents=True,
                orderBy="startTime",
            ).execute().get("items", [])

            if events:
                parts = []
                for e in events:
                    start = e["start"].get("dateTime", e["start"].get("date", ""))
                    try:
                        time_str = datetime.fromisoformat(start).strftime("%H:%M")
                    except Exception:
                        time_str = ""
                    label = f"{time_str} – {e.get('summary', '(senza titolo)')}" if time_str else e.get("summary", "")
                    parts.append(label)
                lines.append("📅 Domani:\n" + "\n".join(f"  • {p}" for p in parts))
                has_content = True
        except Exception as e:
            log.error("Digest agenda domani error: %s", e)

        # ── Obiettivi completati oggi ─────────────────────────────────
        try:
            goals_file = Config.MEMORY_DIR / "goals.json"
            if goals_file.exists():
                data  = json.loads(goals_file.read_text(encoding="utf-8"))
                today = datetime.now().date().isoformat()
                done  = [
                    g for g in data.get("goals", [])
                    if g.get("status") == "completed"
                    and g.get("completed_at", "").startswith(today)
                ]
                if done:
                    lines.append(f"\n✅ Ho completato {len(done)} obiettivo/i oggi:")
                    for g in done[:3]:
                        lines.append(f"  • {g['title']}")
                    has_content = True
        except Exception:
            pass

        # ── Pensieri del giorno ───────────────────────────────────────
        try:
            thoughts_file = Config.MEMORY_DIR / "thoughts.md"
            if thoughts_file.exists():
                content = thoughts_file.read_text(encoding="utf-8")
                today   = datetime.now().strftime("%Y-%m-%d")
                today_thoughts = [
                    line for line in content.splitlines()
                    if today in line and "Pensiero" in line
                ]
                if today_thoughts:
                    lines.append(f"\n🧠 Ho riflettuto {len(today_thoughts)} volta/e oggi.")
                    has_content = True
        except Exception:
            pass

        # ── Cosa esploro domani (da cipher_interests) ─────────────────
        if self._brain:
            try:
                from modules.cipher_interests import CipherInterests
                interests = CipherInterests()
                interest  = interests.get_random_interest(min_intensity=0.6)
                if interest:
                    lines.append(f"\n💡 Domani vorrei esplorare: {interest['topic']}")
                    has_content = True
            except Exception:
                pass

        if not has_content:
            return   # Niente da dire, non inviare

        # ── Genera messaggio serale via LLM se possibile ──────────────
        if self._brain:
            try:
                ctx_block = "\n".join(lines)
                prompt = (
                    f"Sei Cipher. Stai scrivendo il messaggio serale a Simone ({_italian_date(now)}).\n"
                    f"\nHai questo contesto:\n{ctx_block}\n"
                    "\nScrivi un messaggio breve e naturale — non una lista, non sezioni separate. "
                    "Un testo unico che fluisce. Ricordagli cosa lo aspetta domani. "
                    "NON dare consigli su come prepararsi, cosa portare, come riposare o come organizzarsi: "
                    "limitati a informare, non a istruire. "
                    "Tono: diretto, come un promemoria essenziale. "
                    "Max 3-4 righe. Solo il testo, niente intestazioni o emoji strutturali."
                )
                llm_message = self._brain._call_llm_silent(prompt)
                if llm_message:
                    _send_telegram(f"🌙 {llm_message.strip()}")
                    log.info("Digest serale inviato (LLM).")
                    return
            except Exception as e:
                log.error("Digest serale LLM error: %s", e)

        _send_telegram("\n".join(lines))
        log.info("Digest serale inviato.")

    def _should_send_briefing(self) -> bool:
        now   = datetime.now()
        today = now.date()
        if now.hour == BRIEFING_HOUR and now.minute == BRIEFING_MINUTE:
            with self._briefing_lock:
                if self._briefing_sent != today:
                    self._briefing_sent = today
                    return True
        return False

    def _should_send_digest(self) -> bool:
        now   = datetime.now()
        today = now.date()
        if now.hour == DIGEST_HOUR and now.minute == DIGEST_MINUTE:
            with self._digest_lock:
                if self._digest_sent != today:
                    self._digest_sent = today
                    return True
        return False

    # ── Task personalizzati ───────────────────────────────────────────────────

    def _should_run_task(self, task: dict) -> bool:
        now      = datetime.now()
        schedule = task.get("schedule", {})
        stype    = schedule.get("type", "")
        stime    = schedule.get("time", "00:00")

        try:
            h, m = map(int, stime.split(":"))
        except Exception:
            return False

        if now.hour != h or now.minute != m:
            return False

        last = task.get("last_run")
        if last:
            try:
                last_dt = datetime.fromisoformat(last)
                if (now - last_dt).total_seconds() < 60:
                    return False
            except Exception:
                pass

        if stype == "daily":
            return True
        elif stype == "weekly":
            return now.weekday() == int(schedule.get("weekday", 0))
        elif stype == "once":
            date_str = schedule.get("date", "")
            try:
                return now.date() == datetime.strptime(date_str, "%Y-%m-%d").date()
            except Exception:
                return False

        return False

    def _execute_task(self, task: dict) -> None:
        action = task.get("action", "")
        params = task.get("params", {})
        label  = task.get("label", "Task")

        log.info("Esecuzione task: %s (%s)", label, action)

        try:
            if action == "telegram_message":
                _send_telegram(params.get("text", label))

            elif action == "whatsapp_send":
                from modules.whatsapp import WhatsAppService
                wa = WhatsAppService()
                wa.send_message(to=params.get("to", ""), body=params.get("text", ""))

            elif action == "calendar_list":
                cal    = self._get_calendar()
                result = cal.list_events(days=int(params.get("days", 1)))
                _send_telegram(f"📅 {label}\n{result}")

            elif action == "gmail_list":
                from modules.google_mail import GmailClient
                gmail  = GmailClient()
                result = gmail.list_emails(
                    max_results=int(params.get("max_results", 5)),
                    unread_only=params.get("unread_only", True),
                )
                _send_telegram(f"📧 {label}\n{result}")

            else:
                log.warning("Task action non supportata: %s", action)
                return

            task["last_run"] = datetime.now().isoformat()

            if task.get("schedule", {}).get("type") == "once":
                self.remove_task(task["id"])
            else:
                self._save_tasks()

        except Exception as e:
            log.error("Errore esecuzione task %s: %s", label, e)

    # ── Loop principale ───────────────────────────────────────────────────────

    def _loop(self) -> None:
        log.info(
            "Scheduler avviato (briefing %02d:%02d, digest %02d:%02d)",
            BRIEFING_HOUR, BRIEFING_MINUTE, DIGEST_HOUR, DIGEST_MINUTE,
        )
        while not self._stop.is_set():
            if self._should_send_briefing():
                self._send_briefing()
            if self._should_send_digest():
                self._send_evening_digest()
            for task in list(self._tasks):
                if self._should_run_task(task):
                    self._execute_task(task)
            self._stop.wait(CHECK_INTERVAL)

    def start(self) -> None:
        if not Config.TELEGRAM_BOT_TOKEN or not Config.TELEGRAM_ALLOWED_ID:
            log.warning("Scheduler disabilitato: credenziali Telegram mancanti")
            return
        threading.Thread(target=self._loop, daemon=True, name="scheduler").start()
        log.info("Scheduler avviato.")

    def stop(self) -> None:
        self._stop.set()
