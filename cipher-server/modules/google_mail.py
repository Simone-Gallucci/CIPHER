"""
modules/google_mail.py – Gmail via Google API

# Gmail: SOLO su richiesta esplicita di Simone — MAI autonomo
# Non usare in ConsciousnessLoop, morning brief, passive monitor o qualsiasi
# processo autonomo. Usare SOLO quando Simone lo chiede esplicitamente.

Richiede scope: https://www.googleapis.com/auth/gmail.modify
"""

import base64
import email as _email_lib
from datetime import datetime
from email.mime.text import MIMEText
from typing import Optional

from rich.console import Console

from modules.google_auth import get_google_service

console = Console()


class GoogleMail:
    def __init__(self) -> None:
        self._service = get_google_service("gmail", "v1")
        console.print("[green]✓ Gmail pronto[/green]")

    # ── Lista email ───────────────────────────────────────────────────────

    def list_emails(self, max_results: int = 10, query: Optional[str] = None) -> str:
        """Elenca le email più recenti, con mittente, oggetto, data e snippet."""
        try:
            kwargs: dict = {
                "userId":     "me",
                "maxResults": max_results,
                "labelIds":   ["INBOX"],
            }
            if query:
                kwargs["q"] = query

            result   = self._service.users().messages().list(**kwargs).execute()
            messages = result.get("messages", [])

            if not messages:
                return "Nessuna email trovata."

            lines = []
            for msg in messages:
                meta = self._get_metadata(msg["id"])
                lines.append(self._format_meta(meta))

            return f"Inbox ({len(lines)} email):\n" + "\n".join(lines)
        except Exception as e:
            return f"Errore lettura inbox: {e}"

    # ── Lettura singola email ─────────────────────────────────────────────

    def read_email(self, email_id: str) -> str:
        """Ritorna il contenuto completo di una email dato il suo ID."""
        if not email_id:
            return "ID email non specificato."
        try:
            msg = self._service.users().messages().get(
                userId="me", id=email_id, format="full"
            ).execute()

            headers  = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            subject  = headers.get("Subject", "(nessun oggetto)")
            sender   = headers.get("From",    "(mittente sconosciuto)")
            date_raw = headers.get("Date",    "")
            body     = self._extract_body(msg.get("payload", {}))

            date_str = self._parse_date(date_raw)
            return (
                f"Da: {sender}\n"
                f"Oggetto: {subject}\n"
                f"Data: {date_str}\n"
                f"---\n{body[:3000]}"
                + ("\n[... messaggio troncato ...]" if len(body) > 3000 else "")
            )
        except Exception as e:
            return f"Errore lettura email {email_id}: {e}"

    # ── Ricerca email ─────────────────────────────────────────────────────

    def search_emails(self, query: str, max_results: int = 10) -> str:
        """Cerca email per mittente, oggetto o contenuto.
        Esempi query: 'from:nome@email.com', 'subject:fattura', 'is:unread'
        """
        if not query:
            return "Query di ricerca vuota."
        return self.list_emails(max_results=max_results, query=query)

    # ── Invio email ───────────────────────────────────────────────────────

    def send_email(self, to: str, subject: str, body: str) -> str:
        """Invia una email da Gmail."""
        if not to or not subject or not body:
            return "Destinatario, oggetto e corpo sono obbligatori."
        try:
            message = MIMEText(body, "plain", "utf-8")
            message["to"]      = to
            message["subject"] = subject

            raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
            self._service.users().messages().send(
                userId="me", body={"raw": raw}
            ).execute()
            return f"Email inviata a {to} — Oggetto: \"{subject}\""
        except Exception as e:
            return f"Errore invio email: {e}"

    # ── Helpers ───────────────────────────────────────────────────────────

    def _get_metadata(self, msg_id: str) -> dict:
        """Recupera solo i metadati di una email (veloce, senza body)."""
        try:
            msg     = self._service.users().messages().get(
                userId="me", id=msg_id, format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            return {
                "id":      msg_id,
                "from":    headers.get("From",    ""),
                "subject": headers.get("Subject", "(nessun oggetto)"),
                "date":    headers.get("Date",    ""),
                "snippet": msg.get("snippet", ""),
            }
        except Exception:
            return {"id": msg_id, "from": "?", "subject": "?", "date": "", "snippet": ""}

    def _format_meta(self, meta: dict) -> str:
        date_str = self._parse_date(meta.get("date", ""))
        sender   = meta.get("from",    "?")
        # Estrai solo il nome dal mittente (es. "Mario Rossi <mario@gmail.com>" → "Mario Rossi")
        if "<" in sender:
            sender = sender.split("<")[0].strip().strip('"')
        subject  = meta.get("subject", "?")
        snippet  = meta.get("snippet", "")[:80]
        return f"  [{date_str}] {sender} — {subject}\n    {snippet}"

    @staticmethod
    def _parse_date(date_raw: str) -> str:
        """Converte un header Date in formato leggibile italiano."""
        if not date_raw:
            return ""
        try:
            # Rimuovi eventuale timezone testuale (es. "(UTC)", "(CET)")
            import re
            clean = re.sub(r"\s*\([^)]+\)\s*$", "", date_raw.strip())
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(clean)
            return dt.strftime("%d/%m/%Y %H:%M")
        except Exception:
            return date_raw[:20]

    @staticmethod
    def _extract_body(payload: dict) -> str:
        """Estrae il corpo testuale dell'email dal payload MIME (ricorsivo)."""
        mime_type = payload.get("mimeType", "")
        body_data = payload.get("body", {}).get("data", "")

        if mime_type == "text/plain" and body_data:
            try:
                return base64.urlsafe_b64decode(body_data + "==").decode("utf-8", errors="replace")
            except Exception:
                return ""

        if mime_type == "text/html" and body_data:
            try:
                html = base64.urlsafe_b64decode(body_data + "==").decode("utf-8", errors="replace")
                # Strip HTML semplice
                import re
                return re.sub(r"<[^>]+>", "", html)
            except Exception:
                return ""

        # Multipart: prova text/plain prima, poi text/html
        parts = payload.get("parts", [])
        plain = ""
        html  = ""
        for part in parts:
            result = GoogleMail._extract_body(part)
            if part.get("mimeType") == "text/plain" and result:
                plain = result
            elif part.get("mimeType", "").startswith("text/html") and result:
                html = result
            elif result:
                plain = plain or result

        return plain or html or "(corpo non disponibile)"
