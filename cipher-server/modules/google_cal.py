"""
modules/google_cal.py – Google Calendar
"""

import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo
from googleapiclient.discovery import build
from rich.console import Console
from modules.google_auth import get_google_service

console = Console()

ROME = ZoneInfo("Europe/Rome")


def _normalize(text: str) -> str:
    """Normalizza il testo per confronto fuzzy:
    - Rimuove emoji e caratteri non-ASCII (unicodedata)
    - Lowercase
    - Rimuove punteggiatura non alfanumerica
    - Comprime spazi multipli
    """
    # Decomposizione unicode + rimozione caratteri non-ASCII (include emoji)
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_only = nfkd.encode("ascii", errors="ignore").decode("ascii")
    # Rimuovi tutto ciò che non è alfanumerico o spazio
    cleaned = re.sub(r"[^a-z0-9\s]", " ", ascii_only.lower())
    # Comprimi spazi multipli
    return re.sub(r"\s+", " ", cleaned).strip()


class GoogleCalendar:
    def __init__(self) -> None:
        self._service = get_google_service("calendar", "v3")
        console.print("[green]✓ Google Calendar pronto[/green]")

    def list_events(self, days: int = 1, max_results: int = 10,
                    exclude_color_ids: list[str] | None = None,
                    query: str = "") -> str:
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=max(days, 1))

        kwargs: dict = {
            "calendarId":   "primary",
            "timeMin":      now.isoformat(),
            "timeMax":      end.isoformat(),
            "singleEvents": True,
            "orderBy":      "startTime",
        }
        if query:
            kwargs["q"] = query

        if query:
            # Con query testuale: pagina completamente per ottenere il totale reale
            kwargs["maxResults"] = 250
            events: list = []
            page_token = None
            while True:
                if page_token:
                    kwargs["pageToken"] = page_token
                result     = self._service.events().list(**kwargs).execute()
                events.extend(result.get("items", []))
                page_token = result.get("nextPageToken")
                if not page_token:
                    break
                time.sleep(0.1)
            # FIX 1: filtro fuzzy client-side aggiuntivo
            q_norm = _normalize(query)
            if q_norm:
                events = [
                    e for e in events
                    if q_norm in _normalize(e.get("summary", ""))
                ]
        else:
            kwargs["maxResults"] = max_results
            result = self._service.events().list(**kwargs).execute()
            events = result.get("items", [])

        if exclude_color_ids:
            events = [e for e in events if e.get("colorId") not in exclude_color_ids]
        if not events:
            label = f'"{query}"' if query else ("oggi" if days <= 1 else f"nei prossimi {days} giorni")
            return f"Nessun evento trovato{' per ' + label if query else ' in agenda ' + label}."
        lines = []
        for e in events:
            start = e["start"].get("dateTime", e["start"].get("date", ""))
            try:
                dt = datetime.fromisoformat(start)
                time_str = dt.strftime("%d/%m %H:%M")
            except Exception:
                time_str = start
            lines.append(f"• {time_str} – {e.get('summary', '(senza titolo)')}")
        header = f"Trovati {len(events)} eventi" + (f' per "{query}"' if query else "") + ":\n"
        return header + "\n".join(lines)

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
        Supporta ricerca fuzzy (FIX 1), parsing date esteso (FIX 2), conteggio reale (FIX 5).
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

            # FIX 2 — parsing data esteso: YYYY-MM-DD, oggi, domani, DD/MM/YYYY, DD/MM
            if date:
                day_start = None
                d = date.strip().lower()
                today = datetime.now(ROME).replace(hour=0, minute=0, second=0, microsecond=0)
                if d in ("oggi", "today"):
                    day_start = today
                elif d in ("domani", "tomorrow"):
                    day_start = today + timedelta(days=1)
                else:
                    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m"):
                        try:
                            parsed = datetime.strptime(d, fmt)
                            # Per formati senza anno usa l'anno corrente
                            if fmt == "%d/%m":
                                parsed = parsed.replace(year=today.year)
                            day_start = parsed.replace(tzinfo=ROME,
                                                       hour=0, minute=0, second=0, microsecond=0)
                            break
                        except ValueError:
                            continue
                if day_start:
                    day_end = day_start + timedelta(days=1)
                    kwargs["timeMin"] = day_start.isoformat()
                    kwargs["timeMax"] = day_end.isoformat()
                else:
                    console.print(f"[yellow]⚠ Formato data non riconosciuto: '{date}' — ignoro il filtro data[/yellow]")
                    kwargs["timeMin"] = datetime.now(timezone.utc).isoformat()
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
                time.sleep(0.1)  # evita rate limit durante la fase di fetch

            # FIX 1 — filtro fuzzy client-side aggiuntivo sul titolo
            if query:
                q_norm = _normalize(query)
                if q_norm:
                    all_events = [
                        e for e in all_events
                        if q_norm in _normalize(e.get("summary", ""))
                    ]

            if not all_events:
                return "Nessun evento trovato con questi criteri."

            # FIX 5 — salva il totale trovato prima di iniziare la cancellazione
            total_found = len(all_events)

            def _is_rate_limit(exc: Exception) -> bool:
                s = str(exc).lower()
                return "ratelimitexceeded" in s or "429" in s or "rate limit" in s

            def _fmt_event(ev: dict) -> str:
                start = ev["start"].get("dateTime", ev["start"].get("date", ""))
                try:
                    return datetime.fromisoformat(start).strftime("%d/%m/%Y alle %H:%M")
                except Exception:
                    return start

            deleted: list[str] = []
            errors:  list[str] = []

            # Riprova finché tutti gli eventi sono cancellati o non si fa più progressi
            # (= tutti i restanti sono errori permanenti, non rate limit)
            pending = list(all_events)
            first_attempt = True
            while pending:
                if not first_attempt:
                    time.sleep(5)
                first_attempt = False

                still_pending: list[dict] = []
                progress = 0
                for ev in pending:
                    try:
                        self._service.events().delete(calendarId="primary", eventId=ev["id"]).execute()
                        deleted.append(f"• {_fmt_event(ev)} – {ev.get('summary', '(senza titolo)')}")
                        progress += 1
                    except Exception as e:
                        if _is_rate_limit(e):
                            still_pending.append(ev)
                        else:
                            errors.append(f"• {ev.get('summary', ev['id'])}: {e}")

                # Se non abbiamo cancellato nemmeno uno in questo giro, usciamo
                # (tutti i restanti sono errori permanenti — nessun progresso possibile)
                if progress == 0:
                    errors.extend(
                        f"• {ev.get('summary', ev['id'])}: rate limit persistente"
                        for ev in still_pending
                    )
                    break

                pending = still_pending

            # FIX 5 — messaggio con totale trovato + totale eliminato
            msg = f"Trovati {total_found} eventi. Eliminati {len(deleted)}."
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
