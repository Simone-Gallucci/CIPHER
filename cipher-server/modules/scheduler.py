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
DIGEST_MINUTE   = 30
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
        now   = datetime.now()
        lines = [f"☀️ Buongiorno Simone! {_italian_date(now)}"]

        # ── Sommario notturno di ieri ─────────────────────────────────
        try:
            summaries_file = Config.MEMORY_DIR / "daily_summaries.md"
            if summaries_file.exists():
                content = summaries_file.read_text(encoding="utf-8")
                # Cerca l'ultimo sommario notturno
                sections = content.strip().split("---")
                last = next(
                    (s.strip() for s in reversed(sections) if "Riflessione notturna" in s),
                    None,
                )
                if last:
                    # Estrai solo il testo (senza intestazione markdown)
                    body = "\n".join(
                        l for l in last.splitlines()
                        if not l.startswith("#") and l.strip()
                    ).strip()
                    if body:
                        lines.append(f"\n🌙 Ieri di notte ho pensato:\n{body[:400]}")
        except Exception as e:
            log.error("Briefing sommario notturno error: %s", e)

        # ── Agenda oggi ───────────────────────────────────────────────
        try:
            cal    = self._get_calendar()
            events = cal._service.events().list(
                calendarId="primary",
                timeMin=datetime.now(timezone.utc).isoformat(),
                timeMax=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
                maxResults=5,
                singleEvents=True,
                orderBy="startTime",
            ).execute().get("items", [])

            if events:
                lines.append("\n📅 Oggi in agenda:")
                for e in events:
                    start = e["start"].get("dateTime", e["start"].get("date", ""))
                    try:
                        dt       = datetime.fromisoformat(start)
                        time_str = dt.strftime("%H:%M")
                    except Exception:
                        time_str = start
                    lines.append(f"  • {time_str} – {e.get('summary', '(senza titolo)')}")
            else:
                lines.append("\n📅 Nessun evento in agenda oggi.")
        except Exception as e:
            log.error("Briefing calendario error: %s", e)

        # ── Email urgenti non lette ───────────────────────────────────
        try:
            from modules.google_mail import GmailClient
            gmail   = GmailClient()
            urgent  = gmail.list_messages(max_results=5, unread_only=True)
            if urgent:
                lines.append(f"\n📧 {len(urgent)} email non letta/e in attesa.")
        except Exception:
            pass

        # ── Pensiero del mattino via LLM (opzionale) ──────────────────
        if self._brain:
            try:
                thought = self._brain._call_llm_silent(
                    f"Sei Cipher. È mattina ({_italian_date(now)}). "
                    f"Scrivi UNA frase breve con il tuo carattere per iniziare la giornata — "
                    f"diretto, non banale. NON iniziare con 'Buongiorno' (è già nel messaggio). "
                    f"Solo la frase, niente altro."
                )
                if thought:
                    lines.append(f"\n💬 {thought.strip()}")
            except Exception:
                pass

        _send_telegram("\n".join(lines))
        log.info("Briefing mattutino inviato.")

    def _send_evening_digest(self) -> None:
        """Digest serale alle 20:30 — solo se c'è qualcosa di rilevante."""
        lines = ["🌆 Digest serale"]

        has_content = False

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

        _send_telegram("\n".join(lines))
        log.info("Digest serale inviato.")

    def _should_send_briefing(self) -> bool:
        now   = datetime.now()
        today = now.date()
        if now.hour == BRIEFING_HOUR and now.minute == BRIEFING_MINUTE:
            if self._briefing_sent != today:
                self._briefing_sent = today
                return True
        return False

    def _should_send_digest(self) -> bool:
        now   = datetime.now()
        today = now.date()
        if now.hour == DIGEST_HOUR and now.minute == DIGEST_MINUTE:
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
