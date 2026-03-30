"""
modules/actions.py – Dispatcher delle azioni di Cipher
"""
from __future__ import annotations

import subprocess
import sys
import os
from typing import TYPE_CHECKING, Optional
from datetime import datetime, timezone

from rich.console import Console

if TYPE_CHECKING:
    from modules.google_cal   import GoogleCalendar
    from modules.google_mail  import GmailClient
    from modules.whatsapp     import WhatsAppService
    from modules.filesystem   import FileSystem
    from modules.notifier     import Notifier
    from modules.scheduler    import Scheduler
    from modules.file_engine  import FileEngine

console = Console()

CONSENT_PHRASES = [
    "ti do il consenso", "ti do consenso", "hai il consenso",
    "confermo", "procedi", "sì, modifica", "si, modifica",
    "sì, scrivi", "si, scrivi", "sì, esegui", "si, esegui",
    "esegui", "sì, procedi", "si, procedi",
    "sì", "si", "yes", "ok", "okay",
]
DENY_PHRASES = [
    "no", "annulla", "annullato", "stop", "interrompi", "non procedere",
]


class ActionDispatcher:
    def __init__(self, web_search_fn, notifier=None, scheduler=None) -> None:
        self._web_search  = web_search_fn
        self._calendar:   Optional[GoogleCalendar]  = None
        self._gmail:      Optional[GmailClient]     = None
        self._whatsapp:   Optional[WhatsAppService] = None
        self._filesystem: Optional[FileSystem]      = None
        self._file_engine: Optional[FileEngine]     = None
        self._notifier    = notifier
        self._scheduler   = scheduler
        self._pending_write: Optional[dict] = None
        self._pending_exec:  Optional[dict] = None

    def set_notifier(self, notifier) -> None:
        self._notifier = notifier

    def set_scheduler(self, scheduler) -> None:
        self._scheduler = scheduler

    def set_llm_silent(self, llm_fn) -> None:
        """Permette al FileEngine di usare l'LLM per analisi intelligente."""
        if self._file_engine is None:
            from modules.file_engine import FileEngine
            self._file_engine = FileEngine(llm_silent_fn=llm_fn)
        else:
            self._file_engine._llm = llm_fn

    # ── Lazy loaders ──────────────────────────────────────────────────

    def _get_calendar(self):
        if self._calendar is None:
            from modules.google_cal import GoogleCalendar
            self._calendar = GoogleCalendar()
        return self._calendar

    def _get_gmail(self):
        if self._gmail is None:
            from modules.google_mail import GmailClient
            self._gmail = GmailClient()
        return self._gmail

    def _get_whatsapp(self):
        if self._whatsapp is None:
            from modules.whatsapp import WhatsAppService
            self._whatsapp = WhatsAppService()
        return self._whatsapp

    def _get_filesystem(self):
        if self._filesystem is None:
            from modules.filesystem import FileSystem
            self._filesystem = FileSystem()
        return self._filesystem

    def _get_file_engine(self):
        if self._file_engine is None:
            from modules.file_engine import FileEngine
            self._file_engine = FileEngine()
        return self._file_engine

    # ── Pending ───────────────────────────────────────────────────────

    def has_pending(self) -> bool:
        return self._pending_write is not None or self._pending_exec is not None

    def check_consent(self, user_input: str) -> Optional[str]:
        if not self._pending_write and not self._pending_exec:
            return None

        text         = user_input.lower().strip()
        gave_consent = any(phrase in text for phrase in CONSENT_PHRASES)
        denied       = any(phrase in text for phrase in DENY_PHRASES)

        if not gave_consent or denied:
            self._pending_write = None
            self._pending_exec  = None
            return "Azione annullata."

        if self._pending_write:
            params = self._pending_write
            self._pending_write = None
            result = self._get_filesystem().project_write(
                path=params.get("path", ""),
                content=params.get("content", ""),
                append=params.get("append", False),
            )
            return f"Scrittura eseguita: {result}"

        if self._pending_exec:
            params = self._pending_exec
            self._pending_exec = None
            return self._run_shell(
                command=params.get("command", ""),
                timeout=int(params.get("timeout", 30)),
            )

        return None

    # ── Shell ─────────────────────────────────────────────────────────

    def _run_shell(self, command: str, timeout: int = 30) -> str:
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=timeout,
            )
            output = result.stdout.strip()
            errors = result.stderr.strip()
            if result.returncode == 0:
                return output if output else "✓ Comando eseguito senza output."
            else:
                msg = f"✗ Errore (exit code {result.returncode})"
                if errors:
                    msg += f":\n{errors}"
                if output:
                    msg += f"\nOutput:\n{output}"
                return msg
        except subprocess.TimeoutExpired:
            return f"✗ Timeout: il comando ha superato {timeout} secondi."
        except Exception as e:
            return f"✗ Errore durante l'esecuzione: {e}"

    # ── Web Fetch (statico) ───────────────────────────────────────────

    def _web_fetch(self, url: str, max_chars: int = 4000) -> str:
        """Scarica e restituisce il contenuto testuale di una pagina web (HTML statico)."""
        try:
            import requests
            from html.parser import HTMLParser

            class _TextExtractor(HTMLParser):
                SKIP_TAGS = {"script", "style", "head", "noscript", "meta", "link"}

                def __init__(self):
                    super().__init__()
                    self._skip = False
                    self._parts = []

                def handle_starttag(self, tag, attrs):
                    if tag in self.SKIP_TAGS:
                        self._skip = True

                def handle_endtag(self, tag):
                    if tag in self.SKIP_TAGS:
                        self._skip = False

                def handle_data(self, data):
                    if not self._skip:
                        text = data.strip()
                        if text:
                            self._parts.append(text)

                def get_text(self):
                    return "\n".join(self._parts)

            if not url.startswith("http"):
                url = "https://" + url

            headers = {"User-Agent": "Mozilla/5.0 (compatible; Cipher/1.0)"}
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()

            parser = _TextExtractor()
            parser.feed(resp.text)
            text = parser.get_text()

            if len(text) > max_chars:
                text = text[:max_chars] + "\n[... contenuto troncato ...]"

            return text if text.strip() else "Pagina caricata ma nessun testo estratto."

        except Exception as e:
            return f"✗ Impossibile recuperare la pagina: {e}"

    # ── Web Fetch Rendered (Playwright via subprocess) ────────────────

    def _web_fetch_rendered(self, url: str, max_chars: int = 4000) -> str:
        """Carica la pagina con Playwright in un subprocess isolato ed estrae il testo."""
        if not url.startswith("http"):
            url = "https://" + url

        script = f"""
import sys
from playwright.sync_api import sync_playwright
from html.parser import HTMLParser

class _TextExtractor(HTMLParser):
    SKIP_TAGS = {{"script", "style", "head", "noscript", "meta", "link"}}
    def __init__(self):
        super().__init__()
        self._skip = False
        self._parts = []
    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip = True
    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS:
            self._skip = False
    def handle_data(self, data):
        if not self._skip:
            text = data.strip()
            if text:
                self._parts.append(text)
    def get_text(self):
        return "\\n".join(self._parts)

try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto({repr(url)}, wait_until="networkidle", timeout=20000)
        page.wait_for_timeout(2000)
        html = page.content()
        browser.close()
    parser = _TextExtractor()
    parser.feed(html)
    text = parser.get_text()
    print(text[:{max_chars}])
except Exception as e:
    print(f"ERRORE: {{e}}", file=sys.stderr)
    sys.exit(1)
"""

        try:
            venv_python = os.path.join(os.path.dirname(sys.executable), "python3")
            result = subprocess.run(
                [venv_python, "-c", script],
                capture_output=True,
                text=True,
                timeout=40,
            )
            if result.returncode != 0:
                return f"✗ Errore Playwright: {result.stderr.strip()}"
            text = result.stdout.strip()
            if len(text) > max_chars:
                text = text[:max_chars] + "\n[... contenuto troncato ...]"
            return text if text else "Pagina caricata ma nessun testo estratto."
        except subprocess.TimeoutExpired:
            return "✗ Timeout: la pagina ha impiegato troppo a caricarsi."
        except Exception as e:
            return f"✗ Errore: {e}"

    # ── Web Fetch All Rendered (Playwright, browser condiviso) ────────

    def _web_fetch_all_rendered(self, urls: list, max_chars: int = 2000) -> str:
        """Carica più pagine con un singolo browser Playwright condiviso."""
        import json as _json

        urls_json = _json.dumps(urls)

        script = f"""
import sys
import json
from playwright.sync_api import sync_playwright
from html.parser import HTMLParser

class _TextExtractor(HTMLParser):
    SKIP_TAGS = {{"script", "style", "head", "noscript", "meta", "link"}}
    def __init__(self):
        super().__init__()
        self._skip = False
        self._parts = []
    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip = True
    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS:
            self._skip = False
    def handle_data(self, data):
        if not self._skip:
            text = data.strip()
            if text:
                self._parts.append(text)
    def get_text(self):
        return "\\n".join(self._parts)

urls = {urls_json}
max_chars = {max_chars}
results = []

try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for url in urls:
            try:
                page = browser.new_page()
                page.goto(url, wait_until="networkidle", timeout=15000)
                page.wait_for_timeout(1500)
                html = page.content()
                page.close()
                parser = _TextExtractor()
                parser.feed(html)
                text = parser.get_text()[:max_chars]
                results.append(f"=== {{url}} ===\\n{{text}}")
            except Exception as e:
                results.append(f"=== {{url}} ===\\n✗ Errore: {{e}}")
        browser.close()
    print("\\n\\n".join(results))
except Exception as e:
    print(f"ERRORE FATALE: {{e}}", file=sys.stderr)
    sys.exit(1)
"""

        try:
            venv_python = os.path.join(os.path.dirname(sys.executable), "python3")
            result = subprocess.run(
                [venv_python, "-c", script],
                capture_output=True,
                text=True,
                timeout=90,
            )
            if result.returncode != 0:
                return f"✗ Errore Playwright: {result.stderr.strip()}"
            return result.stdout.strip() or "Nessun contenuto estratto."
        except subprocess.TimeoutExpired:
            return "✗ Timeout: le pagine hanno impiegato troppo a caricarsi."
        except Exception as e:
            return f"✗ Errore: {e}"

    # ── Web Explore SPA (Playwright, clicca menu) ─────────────────────

    def _web_explore_spa(self, url: str, max_chars: int = 2000) -> str:
        """Carica una SPA con Playwright, clicca ogni voce del menu e cattura il contenuto."""
        if not url.startswith("http"):
            url = "https://" + url

        script = f"""
import sys
from playwright.sync_api import sync_playwright
from html.parser import HTMLParser

class _TextExtractor(HTMLParser):
    SKIP_TAGS = {{"script", "style", "head", "noscript", "meta", "link"}}
    def __init__(self):
        super().__init__()
        self._skip = False
        self._parts = []
    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip = True
    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS:
            self._skip = False
    def handle_data(self, data):
        if not self._skip:
            text = data.strip()
            if text:
                self._parts.append(text)
    def get_text(self):
        return "\\n".join(self._parts)

def extract(html):
    p = _TextExtractor()
    p.feed(html)
    return p.get_text()

max_chars = {max_chars}
results = []

try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto({repr(url)}, wait_until="networkidle", timeout=20000)
        page.wait_for_timeout(2000)

        # Cattura homepage
        results.append(f"=== Homepage ===\\n{{extract(page.content())[:max_chars]}}")

        # Trova link nel nav
        nav_links = page.query_selector_all("nav a, .nav a, .navbar a, header a, .menu a")
        seen = set()
        clickable = []
        for link in nav_links:
            label = (link.inner_text() or "").strip()
            if label and label not in seen:
                seen.add(label)
                clickable.append((label, link))

        for label, link in clickable:
            try:
                link.click()
                page.wait_for_timeout(1500)
                text = extract(page.content())[:max_chars]
                results.append(f"=== {{label}} ===\\n{{text}}")
            except Exception as e:
                results.append(f"=== {{label}} ===\\n✗ Errore click: {{e}}")

        browser.close()
    print("\\n\\n".join(results))
except Exception as e:
    print(f"ERRORE: {{e}}", file=sys.stderr)
    sys.exit(1)
"""

        try:
            venv_python = os.path.join(os.path.dirname(sys.executable), "python3")
            result = subprocess.run(
                [venv_python, "-c", script],
                capture_output=True,
                text=True,
                timeout=90,
            )
            if result.returncode != 0:
                return f"✗ Errore Playwright: {result.stderr.strip()}"
            return result.stdout.strip() or "Nessun contenuto estratto."
        except subprocess.TimeoutExpired:
            return "✗ Timeout: il sito ha impiegato troppo a caricarsi."
        except Exception as e:
            return f"✗ Errore: {e}"

    # ── Execute ───────────────────────────────────────────────────────

    def execute(self, action: str, params: dict) -> str:
        console.print(f"[dim]⚡ Azione: {action}[/dim]")
        try:
            # ── Web ───────────────────────────────────────────────────
            if action == "web_search":
                return self._web_search(params.get("query", ""))

            elif action == "web_fetch":
                return self._web_fetch(
                    url=params.get("url", ""),
                    max_chars=int(params.get("max_chars", 4000)),
                )

            elif action == "web_fetch_rendered":
                return self._web_fetch_rendered(
                    url=params.get("url", ""),
                    max_chars=int(params.get("max_chars", 4000)),
                )

            elif action == "web_fetch_all":
                urls      = params.get("urls", [])
                max_chars = int(params.get("max_chars", 2000))
                if not urls:
                    return "Nessun URL specificato."
                results = []
                for url in urls:
                    content = self._web_fetch(url, max_chars=max_chars)
                    results.append(f"=== {url} ===\n{content}")
                return "\n\n".join(results)

            elif action == "web_fetch_all_rendered":
                urls      = params.get("urls", [])
                max_chars = int(params.get("max_chars", 2000))
                if not urls:
                    return "Nessun URL specificato."
                return self._web_fetch_all_rendered(urls, max_chars)

            elif action == "web_explore_spa":
                return self._web_explore_spa(
                    url=params.get("url", ""),
                    max_chars=int(params.get("max_chars", 8000)),
                )

            # ── Calendario ────────────────────────────────────────────
            elif action == "calendar_list":
                return self._get_calendar().list_events(days=int(params.get("days", 1)))

            elif action == "calendar_create":
                return self._get_calendar().create_event(
                    title=params.get("title", "Evento"),
                    start=params.get("start", ""),
                    end=params.get("end"),
                    description=params.get("description", ""),
                    location=params.get("location", ""),
                )

            elif action == "calendar_delete":
                return self._get_calendar().delete_event_by_query(
                    query=params.get("query", ""),
                    date=params.get("date", ""),
                    max_results=int(params.get("max_results", 250)),
                )

            # ── Gmail ─────────────────────────────────────────────────
            elif action == "gmail_list":
                return self._get_gmail().list_emails(
                    max_results=int(params.get("max_results", 5)),
                    unread_only=params.get("unread_only", True),
                )

            elif action == "gmail_read":
                return self._get_gmail().read_email(message_id=params.get("message_id", ""))

            elif action == "gmail_send":
                return self._get_gmail().send_email(
                    to=params.get("to", ""),
                    subject=params.get("subject", ""),
                    body=params.get("body", ""),
                )

            # ── WhatsApp ──────────────────────────────────────────────
            elif action == "whatsapp_send":
                return self._get_whatsapp().send_message(
                    to=params.get("to", ""),
                    body=params.get("text", ""),
                )

            # ── Filesystem ────────────────────────────────────────────
            elif action == "fs_list":
                return self._get_filesystem().list_dir(params.get("path", ""))

            elif action == "fs_read":
                return self._get_filesystem().read_file(params.get("path", ""))

            elif action == "fs_write":
                return self._get_filesystem().write_file(
                    path=params.get("path", ""),
                    content=params.get("content", ""),
                    append=params.get("append", False),
                )

            elif action == "fs_mkdir":
                return self._get_filesystem().make_dir(params.get("path", ""))

            elif action == "fs_delete":
                return self._get_filesystem().delete(params.get("path", ""))

            elif action == "fs_move":
                return self._get_filesystem().move(
                    src=params.get("src", ""),
                    dst=params.get("dst", ""),
                )

            elif action == "project_list":
                return self._get_filesystem().project_list(params.get("path", ""))

            elif action == "project_read":
                return self._get_filesystem().project_read(params.get("path", ""))

            elif action == "project_write":
                self._pending_write = params
                path       = params.get("path", "")
                append     = params.get("append", False)
                action_str = "aggiungere testo a" if append else "modificare"
                return f"Sto per {action_str} 'cipher/{path}'. Confermi? (rispondi 'sì' o 'no')"

            elif action == "shell_exec":
                command = params.get("command", "").strip()
                if not command:
                    return "Nessun comando specificato."
                timeout = int(params.get("timeout", 30))
                self._pending_exec = {"command": command, "timeout": timeout}
                return f"Sto per eseguire:\n  {command}\nConfermi? (rispondi 'sì' o 'no')"

            # ── File Engine ───────────────────────────────────────────
            elif action == "file_read":
                return self._get_file_engine().process(
                    path=params.get("path", ""),
                    instruction=params.get("instruction", ""),
                )

            elif action == "file_modify":
                return self._get_file_engine().modify_file(
                    path=params.get("path", ""),
                    instruction=params.get("instruction", ""),
                )

            elif action == "file_delete":
                return self._get_file_engine().delete_file(
                    path=params.get("path", ""),
                )

            elif action == "file_list":
                return self._get_file_engine().list_uploads()

            elif action == "file_to_calendar":
                events = self._get_file_engine().extract_calendar_events(
                    path=params.get("path", ""),
                )
                if not events:
                    return "Nessun evento trovato nel file."

                cal    = self._get_calendar()
                ok     = []
                errors = []

                for ev in events:
                    try:
                        cal.create_event(
                            title=ev.get("title", "Evento"),
                            start=ev.get("start", ""),
                            end=ev.get("end", ev.get("start", "")),
                            description=ev.get("description", ""),
                            location=ev.get("location", ""),
                        )
                        ok.append(ev)
                    except Exception as e:
                        errors.append(f"✗ {ev.get('title', 'Evento')} {ev.get('start', '')}: {e}")

                if not ok:
                    return "Nessun evento inserito.\n" + "\n".join(errors)

                starts = []
                for ev in ok:
                    try:
                        starts.append(datetime.strptime(ev["start"], "%Y-%m-%d %H:%M"))
                    except Exception:
                        pass

                starts.sort()
                data_inizio  = starts[0].strftime("%d/%m/%Y") if starts else "N/D"
                data_fine    = starts[-1].strftime("%d/%m/%Y") if starts else "N/D"
                giorni_unici = len({ev["start"][:10] for ev in ok})
                ore_totali   = sum(
                    (
                        datetime.strptime(ev.get("end", ev["start"]), "%Y-%m-%d %H:%M") -
                        datetime.strptime(ev["start"], "%Y-%m-%d %H:%M")
                    ).seconds // 3600
                    for ev in ok
                )

                resoconto = (
                    f"✅ Inseriti {len(ok)} eventi nel calendario.\n\n"
                    f"📅 Periodo: {data_inizio} → {data_fine}\n"
                    f"📆 Giorni lavorativi: {giorni_unici}\n"
                    f"⏱ Ore totali: {ore_totali}h\n"
                    f"📍 Sede: {ok[0].get('location', 'N/D')}\n"
                    f"👤 Tutor: {ok[0].get('description', 'N/D').replace('Tutor: ', '')}\n"
                )

                if errors:
                    resoconto += f"\n⚠️ Errori ({len(errors)}):\n" + "\n".join(errors)

                return resoconto

            # ── Timer e promemoria ────────────────────────────────────
            elif action == "timer_set":
                if not self._notifier:
                    return "Notifier non disponibile."
                seconds = int(params.get("seconds", 0))
                label   = params.get("label", "Timer")
                if seconds <= 0:
                    return "Durata non valida."
                self._notifier.add_timer(seconds, label)
                mins = seconds // 60
                secs = seconds % 60
                if mins > 0:
                    duration = f"{mins} minuti" + (f" e {secs} secondi" if secs else "")
                else:
                    duration = f"{secs} secondi"
                return f"Timer impostato: {label} tra {duration}."

            elif action == "reminder_set":
                if not self._notifier:
                    return "Notifier non disponibile."
                remind_at_str = params.get("remind_at", "")
                label         = params.get("label", "Promemoria")
                add_calendar  = params.get("calendar", True)
                try:
                    remind_at = datetime.fromisoformat(remind_at_str)
                    if remind_at.tzinfo is None:
                        remind_at = remind_at.replace(tzinfo=timezone.utc)
                except Exception:
                    return f"Formato data non valido: {remind_at_str}"
                self._notifier.add_reminder(remind_at, label, add_calendar)
                time_str = remind_at.astimezone().strftime("%d/%m/%Y alle %H:%M")
                cal_str  = " (aggiunto anche al calendario)" if add_calendar else ""
                return f"Promemoria impostato: {label} il {time_str}{cal_str}."

            elif action == "timer_list":
                if not self._notifier:
                    return "Notifier non disponibile."
                timers = self._notifier.list_timers()
                if not timers:
                    return "Nessun timer o promemoria attivo."
                now   = datetime.now(timezone.utc)
                lines = []
                for t in timers:
                    remaining = int((t["expire_at"] - now).total_seconds())
                    if remaining < 0:
                        continue
                    mins = remaining // 60
                    secs = remaining % 60
                    icon = "⏰" if t["type"] == "timer" else "🔔"
                    time_left = f"{mins}m {secs}s" if mins > 0 else f"{secs}s"
                    lines.append(f"{icon} [{t['id']}] {t['label']} – tra {time_left}")
                return "Timer attivi:\n" + "\n".join(lines) if lines else "Nessun timer attivo."

            elif action == "timer_cancel":
                if not self._notifier:
                    return "Notifier non disponibile."
                timer_id = params.get("id", "")
                return self._notifier.cancel_timer(timer_id)

            else:
                return f"Azione sconosciuta: {action}"

        except Exception as e:
            console.print_exception()
            return f"Errore durante l'esecuzione di '{action}': {e}"
