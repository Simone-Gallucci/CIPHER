"""
modules/google_auth.py – Autenticazione OAuth2 Google (condivisa)
Al primo avvio apre il browser per l'autorizzazione.
Il token viene salvato in token.json e riusato nelle sessioni successive.
"""

from pathlib import Path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from rich.console import Console
from config import Config

console = Console()
_credentials_cache = None


def get_credentials() -> Credentials:
    global _credentials_cache
    if _credentials_cache and _credentials_cache.valid:
        return _credentials_cache

    creds = None
    token_path = Path(Config.GOOGLE_TOKEN_FILE)
    creds_path = Path(Config.GOOGLE_CREDENTIALS_FILE)

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), Config.GOOGLE_SCOPES)
        # Controlla che gli scope del token corrispondano a quelli configurati.
        # Se non corrispondono (es. dopo aver rimosso Gmail), elimina il token
        # e rilancia il flusso di autorizzazione per evitare errori silenziosi.
        if creds and creds.scopes is not None:
            required = set(Config.GOOGLE_SCOPES)
            granted  = set(creds.scopes)
            if required != granted:
                console.print(
                    "[yellow]⚠ Scope Google nel token non corrispondono alla configurazione "
                    f"(token: {sorted(granted)}, richiesti: {sorted(required)}). "
                    "Elimino il token e riautorizzo...[/yellow]"
                )
                token_path.unlink(missing_ok=True)
                creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            console.print("[cyan]🔄 Refresh token Google...[/cyan]")
            creds.refresh(Request())
        else:
            if not creds_path.exists():
                raise FileNotFoundError(
                    f"credentials.json non trovato: {creds_path}\n"
                    "  → Scaricalo da Google Cloud Console\n"
                    "  → APIs & Services → Credentials → OAuth 2.0 Client IDs → Desktop app"
                )
            console.print("[cyan]🌐 Apertura browser per autorizzazione Google...[/cyan]")
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), Config.GOOGLE_SCOPES)
            creds = flow.run_local_server(port=0)
            console.print("[green]✓ Autorizzazione Google completata[/green]")

        token_path.write_text(creds.to_json())

    _credentials_cache = creds
    return creds


def get_google_service(service_name: str, version: str):
    creds = get_credentials()
    return build(service_name, version, credentials=creds)
