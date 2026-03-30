"""
modules/google_mail.py – Gmail
"""

import base64
from email.mime.text import MIMEText
from rich.console import Console
from modules.google_auth import get_google_service

console = Console()


class GmailClient:
    def __init__(self) -> None:
        self._service = get_google_service("gmail", "v1")
        console.print("[green]✓ Gmail pronto[/green]")

    def list_emails(self, max_results: int = 5, unread_only: bool = True) -> str:
        query = "is:unread" if unread_only else ""
        result = self._service.users().messages().list(
            userId="me", maxResults=max_results, q=query
        ).execute()
        messages = result.get("messages", [])
        if not messages:
            return f"Nessuna email {'non letta' if unread_only else 'recente'}."
        lines = []
        for msg in messages:
            meta = self._service.users().messages().get(
                userId="me", id=msg["id"], format="metadata",
                metadataHeaders=["From", "Subject"]
            ).execute()
            headers = {h["name"]: h["value"] for h in meta.get("payload", {}).get("headers", [])}
            subject = headers.get("Subject", "(nessun oggetto)")
            sender  = headers.get("From", "sconosciuto")[:40]
            lines.append(f"• [{msg['id'][:8]}] Da: {sender} — {subject}")
        return f"Email {'non lette' if unread_only else 'recenti'}:\n" + "\n".join(lines)

    def read_email(self, message_id: str) -> str:
        try:
            msg = self._service.users().messages().get(
                userId="me", id=message_id, format="full"
            ).execute()
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            subject = headers.get("Subject", "(nessun oggetto)")
            sender  = headers.get("From", "sconosciuto")
            body    = self._extract_body(msg.get("payload", {}))
            self._service.users().messages().modify(
                userId="me", id=message_id, body={"removeLabelIds": ["UNREAD"]}
            ).execute()
            return f"Da: {sender}\nOggetto: {subject}\n---\n{body[:800]}{'...' if len(body) > 800 else ''}"
        except Exception as e:
            return f"Impossibile leggere l'email: {e}"

    def send_email(self, to: str, subject: str, body: str) -> str:
        try:
            msg = MIMEText(body)
            msg["to"] = to
            msg["subject"] = subject
            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            self._service.users().messages().send(userId="me", body={"raw": raw}).execute()
            return f"Email inviata a {to} con oggetto \"{subject}\"."
        except Exception as e:
            return f"Impossibile inviare l'email: {e}"

    def _extract_body(self, payload: dict) -> str:
        if "parts" in payload:
            for part in payload["parts"]:
                if part.get("mimeType") == "text/plain":
                    data = part.get("body", {}).get("data", "")
                    if data:
                        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            return self._extract_body(payload["parts"][0])
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        return "(corpo non disponibile)"
