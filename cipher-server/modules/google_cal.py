"""
modules/google_cal.py – Google Calendar
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo
from googleapiclient.discovery import build
from rich.console import Console
from modules.google_auth import get_google_service

console = Console()

ROME = ZoneInfo("Europe/Rome")


class GoogleCalendar:
    def __init__(self) -> None:
        self._service = get_google_service("calendar", "v3")
        console.print("[green]✓ Google Calendar pronto[/green]")

    def list_events(self, days: int = 1, max_results: int = 10) -> str:
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=max(days, 1))
        result = self._service.events().list(
            calendarId="primary",
            timeMin=now.isoformat(),
            timeMax=end.isoformat(),
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        events = result.get("items", [])
        if not events:
            return f"Nessun evento in agenda {'oggi' if days <= 1 else f'nei prossimi {days} giorni'}."
        lines = []
        for e in events:
            start = e["start"].get("dateTime", e["start"].get("date", ""))
            try:
                dt = datetime.fromisoformat(start)
                time_str = dt.strftime("%d/%m %H:%M")
            except Exception:
                time_str = start
            lines.append(f"• {time_str} – {e.get('summary', '(senza titolo)')}")
        return "Agenda:\n" + "\n".join(lines)

    def create_event(self, title: str, start: str, end: Optional[str] = None,
                     description: str = "", location: str = "") -> str:
        try:
            start_dt = self._parse_datetime(start)
            end_dt   = self._parse_datetime(end) if end else start_dt + timedelta(hours=1)
        except ValueError as e:
            return f"Formato data non valido: {e}"

        event_body = {
            "summary":     title,
            "description": description,
            "location":    location,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": "Europe/Rome"},
            "end":   {"dateTime": end_dt.isoformat(),   "timeZone": "Europe/Rome"},
        }
        created = self._service.events().insert(calendarId="primary", body=event_body).execute()
        return (f"Evento creato: \"{title}\" il {start_dt.strftime('%d/%m/%Y alle %H:%M')}. "
                f"Link: {created.get('htmlLink', '')}")

    def delete_event(self, event_id: str) -> str:
        try:
            self._service.events().delete(calendarId="primary", eventId=event_id).execute()
            return "Evento eliminato con successo."
        except Exception as e:
            return f"Impossibile eliminare l'evento: {e}"

    def delete_event_by_query(self, query: str = "", date: str = "", max_results: int = 250) -> str:
        """
        Cerca eventi per titolo e/o data e li elimina tutti, con paginazione completa.
        """
        try:
            kwargs = {
                "calendarId":   "primary",
                "maxResults":   250,
                "singleEvents": True,
                "orderBy":      "startTime",
            }
            if query:
                kwargs["q"] = query
            if date:
                try:
                    day_start = datetime.strptime(date.strip(), "%Y-%m-%d").replace(tzinfo=ROME)
                    day_end   = day_start + timedelta(days=1)
                    kwargs["timeMin"] = day_start.isoformat()
                    kwargs["timeMax"] = day_end.isoformat()
                except ValueError:
                    pass
            else:
                kwargs["timeMin"] = datetime.now(timezone.utc).isoformat()

            # Raccoglie TUTTI gli eventi con paginazione
            all_events = []
            page_token = None
            while True:
                if page_token:
                    kwargs["pageToken"] = page_token
                result    = self._service.events().list(**kwargs).execute()
                all_events.extend(result.get("items", []))
                page_token = result.get("nextPageToken")
                if not page_token:
                    break

            if not all_events:
                return "Nessun evento trovato con questi criteri."

            deleted = []
            errors  = []
            for ev in all_events:
                try:
                    self._service.events().delete(calendarId="primary", eventId=ev["id"]).execute()
                    start = ev["start"].get("dateTime", ev["start"].get("date", ""))
                    try:
                        dt = datetime.fromisoformat(start)
                        time_str = dt.strftime("%d/%m/%Y alle %H:%M")
                    except Exception:
                        time_str = start
                    deleted.append(f"• {time_str} – {ev.get('summary', '(senza titolo)')}")
                except Exception as e:
                    errors.append(f"• Errore: {e}")

            msg = f"Eliminati {len(deleted)} eventi."
            if errors:
                msg += f"\nErrori ({len(errors)}): " + "; ".join(errors)
            return msg

        except Exception as e:
            return f"Errore durante la ricerca/eliminazione: {e}"

    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                # Interpreta l'orario come ora italiana, non UTC
                return datetime.strptime(value.strip(), fmt).replace(tzinfo=ROME)
            except ValueError:
                continue
        raise ValueError(f"Impossibile interpretare la data: {value}")
