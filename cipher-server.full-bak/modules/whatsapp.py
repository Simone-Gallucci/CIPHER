"""
Cipher AI – WhatsApp Integration (via Green API)
Send and receive WhatsApp messages using your own phone number.
"""
import logging
from typing import Any, Dict, List, Optional

import requests

from config import Config
GREEN_API_INSTANCE_ID = Config.GREEN_API_INSTANCE_ID
GREEN_API_TOKEN       = Config.GREEN_API_TOKEN

log = logging.getLogger("cipher.whatsapp")

API_BASE = "https://7107.api.greenapi.com"


class WhatsAppService:
    """Send WhatsApp messages through Green API (your own number)."""

    def __init__(self):
        self.instance_id = GREEN_API_INSTANCE_ID
        self.token = GREEN_API_TOKEN
        self.ready = bool(self.instance_id and self.token)
        if self.ready:
            self.base_url = f"{API_BASE}/waInstance{self.instance_id}"
            log.info("WhatsApp (Green API) initialised – instance %s", self.instance_id)
        else:
            self.base_url = ""
            log.warning("Green API credentials not set – WhatsApp features unavailable")

    def _url(self, method: str) -> str:
        return f"{self.base_url}/{method}/{self.token}"

    @staticmethod
    def _clean_number(number: str) -> str:
        """Normalise phone number to chatId format (e.g. 393331234567@c.us)."""
        number = number.replace("+", "").replace(" ", "").replace("-", "")
        if not number.endswith("@c.us"):
            number = f"{number}@c.us"
        return number

    # ── Send Message ────────────────────────────────────────────────────────
    def send_message(self, to: str, body: str) -> Dict[str, Any]:
        """
        Send a WhatsApp message.
        *to* should be a phone number with country code, e.g. '+393401234567'.
        """
        if not self.ready:
            return {"error": "WhatsApp non configurato (credenziali Green API mancanti)."}

        chat_id = self._clean_number(to)
        payload = {"chatId": chat_id, "message": body}

        try:
            resp = requests.post(self._url("sendMessage"), json=payload, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            log.info("WhatsApp message sent to %s: %s", to, data)
            return {"id": data.get("idMessage", ""), "status": "sent", "to": to}
        except Exception as exc:
            log.error("WhatsApp send failed: %s", exc)
            return {"error": str(exc)}

    # ── Read Recent Messages ────────────────────────────────────────────────
    def list_messages(self, limit: int = 10) -> List[Dict[str, str]]:
        """List recent incoming WhatsApp messages via Green API journal."""
        if not self.ready:
            return []

        try:
            resp = requests.get(
                self._url("lastIncomingMessages") + f"?minutes=1440",
                timeout=10,
            )
            resp.raise_for_status()
            messages = resp.json()
            results = []
            for m in messages[:limit]:
                results.append({
                    "from": m.get("senderData", {}).get("chatName", m.get("chatId", "")),
                    "body": m.get("textMessage", m.get("caption", "[media]")),
                    "date": str(m.get("timestamp", "")),
                    "type": m.get("typeMessage", ""),
                })
            return results
        except Exception as exc:
            log.error("WhatsApp list failed: %s", exc)
            return []

    # ── Check Connection Status ─────────────────────────────────────────────
    def check_status(self) -> Dict[str, Any]:
        """Check if the Green API instance is connected."""
        if not self.ready:
            return {"status": "not_configured"}
        try:
            resp = requests.get(self._url("getStateInstance"), timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            log.error("WhatsApp status check failed: %s", exc)
            return {"error": str(exc)}

    def format_messages(self, messages: List[Dict]) -> str:
        if not messages:
            return "Nessun messaggio WhatsApp recente."
        lines = []
        for m in messages:
            lines.append(f"• Da {m['from']} ({m['date']}): {m['body'][:120]}")
        return "\n".join(lines)
