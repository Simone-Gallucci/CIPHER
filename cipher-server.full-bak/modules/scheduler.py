"""
modules/scheduler.py – Task automatici di Cipher
- Digest serale alle 20:00
- Task personalizzati definiti via Cipher (voce/testo/Telegram)
  salvati in ~/cipher/scheduling/tasks.json

Il morning brief è gestito interamente da consciousness_loop.py (_send_morning_brief).
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
        self._digest_sent   = None
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

    def _send_evening_digest(self) -> None:
        """Digest serale alle 20:00."""
        now = datetime.now()
        lines = []
        has_content = False

        # ── Agenda di domani ──────────────────────────────────────────
        try:
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
                lines.append("Domani:\n" + "\n".join(f"  • {p}" for p in parts))
                has_content = True
        except Exception as e:
            log.error("Digest agenda domani error: %s", e)

        # ── Promemoria pendenti (scadono domani o già scaduti) ────────
        try:
            from modules.reminders import list_pending
            tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")
            today_str    = now.strftime("%Y-%m-%d")
            pending = [
                r for r in list_pending()
                if r.get("remind_at", "").startswith(tomorrow_str)
                or r.get("remind_at", "").startswith(today_str)
            ]
            if pending:
                parts = [f"  • {r['label']} ({r['remind_at'][11:16]})" for r in pending]
                lines.append("Promemoria:\n" + "\n".join(parts))
                has_content = True
        except Exception as e:
            log.error("Digest promemoria error: %s", e)

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
                llm_message = self._brain._call_llm_visible(prompt)
                if llm_message:
                    _send_telegram(f"🌙 {llm_message.strip()}")
                    log.info("Digest serale inviato (LLM).")
                    return
            except Exception as e:
                log.error("Digest serale LLM error: %s", e)

        _send_telegram("\n".join(lines))
        log.info("Digest serale inviato.")

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
        log.info("Scheduler avviato (digest %02d:%02d)", DIGEST_HOUR, DIGEST_MINUTE)
        while not self._stop.is_set():
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
