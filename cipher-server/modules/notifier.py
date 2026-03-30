"""
modules/notifier.py – Notifiche proattive su Telegram
- Controlla eventi imminenti ogni minuto
- Gestisce timer e promemoria
- I promemoria sono persistiti su JSON tramite modules/reminders
- Riceve messaggi e FILE da Telegram (polling) e li passa al Brain
"""

import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Callable

import requests
from config import Config

log = logging.getLogger("cipher.notifier")

CALENDAR_CHECK_INTERVAL = 60
EVENT_ADVANCE_MINUTES   = 15
TIMER_CHECK_INTERVAL    = 1
POLLING_INTERVAL        = 2

TELEGRAM_API = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}"


def _send_telegram(text: str) -> None:
    try:
        requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": Config.TELEGRAM_ALLOWED_ID, "text": text},
            timeout=10,
        )
    except Exception as e:
        log.error("Errore invio notifica Telegram: %s", e)


class Notifier:
    def __init__(self) -> None:
        self._stop             = threading.Event()
        self._notified_events: set[str] = set()
        self._calendar         = None

        self._timers:     list[dict] = []
        self._timers_lock = threading.Lock()
        self._timer_id    = 0

        self._message_callback: Optional[Callable[[str], str]] = None
        self._file_callback:    Optional[Callable[[str, str], str]] = None

        self._polling_offset = 0

        # Stato per gestire file in attesa di istruzione
        # { "filename": "...", "path": "..." }
        self._pending_file: Optional[dict] = None

        self._restore_reminders()

    # ── Callback setup ────────────────────────────────────────────────

    def set_message_callback(self, fn: Callable[[str], str]) -> None:
        self._message_callback = fn

    def set_file_callback(self, fn: Callable[[str, str], str]) -> None:
        self._file_callback = fn

    # ── Restore promemoria ────────────────────────────────────────────

    def _restore_reminders(self) -> None:
        try:
            import modules.reminders as rem_store
            pending = rem_store.list_pending()
            if not pending:
                return
            now      = datetime.now()
            restored = 0
            for r in pending:
                try:
                    remind_dt = datetime.strptime(r["remind_at"], "%Y-%m-%d %H:%M")
                except ValueError:
                    log.warning("Formato remind_at non valido per #%d, ignorato.", r["id"])
                    continue
                if remind_dt <= now:
                    _send_telegram(f"🔔 Promemoria (in ritardo): {r['label']}")
                    rem_store.mark_notified(r["id"])
                    continue
                remind_utc = remind_dt.astimezone(timezone.utc)
                with self._timers_lock:
                    self._timer_id += 1
                    self._timers.append({
                        "id":        self._timer_id,
                        "label":     r["label"],
                        "expire_at": remind_utc,
                        "type":      "reminder",
                        "json_id":   r["id"],
                    })
                restored += 1
            if restored:
                log.info("Ripristinati %d promemoria dal file JSON.", restored)
        except Exception as e:
            log.error("Errore restore promemoria: %s", e)

    # ── Calendar lazy ─────────────────────────────────────────────────

    def _get_calendar(self):
        if self._calendar is None:
            from modules.google_cal import GoogleCalendar
            self._calendar = GoogleCalendar()
        return self._calendar

    # ── Calendar loop ─────────────────────────────────────────────────

    def _check_calendar(self) -> None:
        try:
            calendar = self._get_calendar()
            now      = datetime.now(timezone.utc)
            window   = now + timedelta(minutes=EVENT_ADVANCE_MINUTES + 1)
            result   = calendar._service.events().list(
                calendarId="primary",
                timeMin=now.isoformat(),
                timeMax=window.isoformat(),
                maxResults=10,
                singleEvents=True,
                orderBy="startTime",
            ).execute()
            for event in result.get("items", []):
                event_id = event["id"]
                if event_id in self._notified_events:
                    continue
                start_str = event["start"].get("dateTime", event["start"].get("date", ""))
                try:
                    start_dt = datetime.fromisoformat(start_str)
                    if start_dt.tzinfo is None:
                        start_dt = start_dt.replace(tzinfo=timezone.utc)
                except Exception:
                    continue
                minutes_left = int((start_dt - now).total_seconds() / 60)
                if 0 <= minutes_left <= EVENT_ADVANCE_MINUTES:
                    self._notified_events.add(event_id)
                    title    = event.get("summary", "(senza titolo)")
                    location = event.get("location", "")
                    time_str = start_dt.astimezone().strftime("%H:%M")
                    msg = f"📅 Evento tra {minutes_left} minuti\n{title} alle {time_str}"
                    if location:
                        msg += f"\nLuogo: {location}"
                    _send_telegram(msg)
                    log.info("Notifica evento inviata: %s", title)
        except Exception as e:
            log.error("Errore controllo calendario: %s", e)

    def _calendar_loop(self) -> None:
        log.info("Calendar notifier avviato (ogni %d sec)", CALENDAR_CHECK_INTERVAL)
        while not self._stop.is_set():
            self._check_calendar()
            self._stop.wait(CALENDAR_CHECK_INTERVAL)

    # ── Timer e promemoria ────────────────────────────────────────────

    def add_timer(self, seconds: int, label: str = "") -> int:
        with self._timers_lock:
            self._timer_id += 1
            tid       = self._timer_id
            expire_at = datetime.now(timezone.utc) + timedelta(seconds=seconds)
            self._timers.append({
                "id":        tid,
                "label":     label,
                "expire_at": expire_at,
                "type":      "timer",
            })
        log.info("Timer #%d aggiunto: %s", tid, label)
        return tid

    def add_reminder(self, remind_at: datetime, label: str, create_calendar_event: bool = True) -> int:
        if remind_at.tzinfo is None:
            remind_at = remind_at.replace(tzinfo=timezone.utc)
        json_id = None
        try:
            import modules.reminders as rem_store
            remind_local = remind_at.astimezone()
            json_id = rem_store.add(remind_local, label, create_calendar_event)
        except Exception as e:
            log.error("Errore persistenza promemoria: %s", e)
        with self._timers_lock:
            self._timer_id += 1
            tid   = self._timer_id
            entry = {
                "id":        tid,
                "label":     label,
                "expire_at": remind_at,
                "type":      "reminder",
            }
            if json_id is not None:
                entry["json_id"] = json_id
            self._timers.append(entry)
        if create_calendar_event:
            try:
                cal = self._get_calendar()
                cal.create_event(
                    title=f"🔔 {label}",
                    start=remind_at.astimezone().strftime("%Y-%m-%d %H:%M"),
                    end=(remind_at + timedelta(minutes=15)).astimezone().strftime("%Y-%m-%d %H:%M"),
                    description="Promemoria creato da Cipher",
                )
            except Exception as e:
                log.error("Errore creazione evento calendario: %s", e)
        log.info("Promemoria #%d aggiunto: %s alle %s", tid, label, remind_at)
        return tid

    def _check_timers(self) -> None:
        now   = datetime.now(timezone.utc)
        fired = []
        with self._timers_lock:
            remaining = []
            for t in self._timers:
                if now >= t["expire_at"]:
                    fired.append(t)
                else:
                    remaining.append(t)
            self._timers = remaining
        for t in fired:
            if t["type"] == "timer":
                label = t["label"] or "Timer"
                _send_telegram(f"⏰ {label} — tempo scaduto!")
            elif t["type"] == "reminder":
                label = t["label"] or "Promemoria"
                _send_telegram(f"🔔 Promemoria: {label}")
                json_id = t.get("json_id")
                if json_id is not None:
                    try:
                        import modules.reminders as rem_store
                        rem_store.mark_notified(json_id)
                    except Exception as e:
                        log.error("Errore mark_notified #%d: %s", json_id, e)

    def _timer_loop(self) -> None:
        log.info("Timer loop avviato")
        while not self._stop.is_set():
            self._check_timers()
            self._stop.wait(TIMER_CHECK_INTERVAL)

    def list_timers(self) -> list[dict]:
        with self._timers_lock:
            return list(self._timers)

    def cancel_timer(self, timer_id: int) -> bool:
        with self._timers_lock:
            target = next((t for t in self._timers if t["id"] == timer_id), None)
            if target is None:
                return False
            self._timers = [t for t in self._timers if t["id"] != timer_id]
        json_id = target.get("json_id")
        if json_id is not None:
            try:
                import modules.reminders as rem_store
                rem_store.cancel(json_id)
            except Exception as e:
                log.error("Errore cancel reminder JSON #%d: %s", json_id, e)
        return True

    # ── Polling Telegram ──────────────────────────────────────────────

    def _polling_loop(self) -> None:
        log.info("Telegram polling avviato")
        while not self._stop.is_set():
            try:
                self._poll_once()
            except Exception as e:
                log.error("Errore polling Telegram: %s", e)
            self._stop.wait(POLLING_INTERVAL)

    def _poll_once(self) -> None:
        try:
            resp = requests.get(
                f"{TELEGRAM_API}/getUpdates",
                params={"offset": self._polling_offset, "timeout": 1},
                timeout=5,
            )
            data = resp.json()
        except Exception:
            return

        if not data.get("ok"):
            return

        for update in data.get("result", []):
            self._polling_offset = update["update_id"] + 1
            self._handle_update(update)

    def _handle_update(self, update: dict) -> None:
        message = update.get("message", {})
        if not message:
            return

        chat_id = message.get("chat", {}).get("id")
        if str(chat_id) != str(Config.TELEGRAM_ALLOWED_ID):
            log.warning("Messaggio da chat non autorizzata: %s", chat_id)
            return

        # ── Messaggio testuale ────────────────────────────────────────
        if "text" in message:
            text = message["text"].strip()
            log.info("Messaggio Telegram: %s", text[:50])

            # Se c'è un file in attesa, usa il testo come istruzione
            if self._pending_file and self._file_callback:
                pending  = self._pending_file
                self._pending_file = None
                try:
                    response = self._file_callback(pending["path"], text)
                    if response:
                        _send_telegram(response)
                except Exception as e:
                    log.error("Errore file callback: %s", e)
                return

            # Messaggio normale → passa al Brain
            if self._message_callback:
                try:
                    response = self._message_callback(text)
                    if response:
                        _send_telegram(response)
                except Exception as e:
                    log.error("Errore callback messaggio: %s", e)
            return

        # ── File / Documento ──────────────────────────────────────────
        file_info = None
        filename  = None

        if "document" in message:
            doc       = message["document"]
            file_info = doc.get("file_id")
            filename  = doc.get("file_name", f"file_{doc.get('file_id', 'unknown')}")
        elif "photo" in message:
            photos    = message["photo"]
            photo     = max(photos, key=lambda p: p.get("file_size", 0))
            file_info = photo.get("file_id")
            filename  = f"photo_{photo['file_id']}.jpg"

        if not file_info:
            return

        log.info("File ricevuto da Telegram: %s", filename)

        # Scarica il file
        saved_path = self._download_telegram_file(file_info, filename)
        if not saved_path:
            _send_telegram("Errore durante il download del file.")
            return

        # Salva in attesa e chiede cosa fare — senza analizzare automaticamente
        self._pending_file = {"filename": filename, "path": str(saved_path)}
        _send_telegram(f"📎 Ho ricevuto {filename}. Cosa vuoi che faccia?")

    def _download_telegram_file(self, file_id: str, filename: str) -> Optional[Path]:
        try:
            resp = requests.get(
                f"{TELEGRAM_API}/getFile",
                params={"file_id": file_id},
                timeout=10,
            )
            file_data = resp.json()
            if not file_data.get("ok"):
                return None

            file_path = file_data["result"]["file_path"]
            file_url  = f"https://api.telegram.org/file/bot{Config.TELEGRAM_BOT_TOKEN}/{file_path}"
            content   = requests.get(file_url, timeout=30).content

            from modules.file_engine import FileEngine
            engine = FileEngine()
            saved  = engine.save_upload(filename, content)
            log.info("File salvato: %s (%d bytes)", saved, len(content))
            return saved

        except Exception as e:
            log.error("Errore download file Telegram: %s", e)
            return None

    # ── Avvio/Stop ────────────────────────────────────────────────────

    def start(self) -> None:
        if not Config.TELEGRAM_BOT_TOKEN or not Config.TELEGRAM_ALLOWED_ID:
            log.warning("Notifier disabilitato: credenziali Telegram mancanti")
            return

        threading.Thread(target=self._calendar_loop, daemon=True, name="notifier-calendar").start()
        threading.Thread(target=self._timer_loop,    daemon=True, name="notifier-timers").start()

        log.info("Notifier avviato (calendar + timers).")

    def stop(self) -> None:
        self._stop.set()
        log.info("Notifier fermato.")
