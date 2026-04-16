"""
modules/path_guard.py – Validazione centralizzata dei path utente e di progetto.

SECURITY-STEP2: sostituisce _safe_file_path (server.py) e _safe_path
(filesystem.py) che usavano str.startswith() bypassabile via directory
con nome adiacente (es. home_evil/). Rimuove anche BASE_DIR da
_resolve_path di file_engine che permetteva la lettura di .env e secrets/
via prompt injection.

Usato da:
  server.py     → /api/files/* endpoint
  filesystem.py → operazioni su file utente e di progetto via LLM actions
  file_engine.py → lettura/modifica/cancellazione file utente

Tutti gli accessi al filesystem utente passano da validate_path().
Tutti gli accessi al filesystem di progetto passano da validate_project_path().
Nessuno si fa la sua validazione propria.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import Config

# ── Percorsi ────────────────────────────────────────────────────────────────
# SECURITY-STEP2: log separato da MEMORY_DIR → non resettato da Tabula Rasa
_LOGS_DIR  = Config.BASE_DIR / "logs"
_AUDIT_LOG = _LOGS_DIR / "file_audit.log"

# ── Regex validazione user_id ────────────────────────────────────────────────
# SECURITY-STEP2: impedisce user_id con '/', '..', caratteri speciali che
# potrebbero essere usati per costruire path fuori da HOME_ROOT.
_USER_ID_RE = re.compile(r"^[a-z0-9_]{2,32}$")


class PathTraversalError(ValueError):
    """
    Sollevata quando un path richiesto esce dalla directory autorizzata.

    SECURITY-STEP2: eccezione dedicata per distinguere errori di sicurezza
    da errori generici di filesystem. Inclusa in ValueError per compatibilità
    con codice esistente che cattura ValueError.
    """

    def __init__(
        self,
        message: str,
        user_id: str = "",
        requested_path: str = "",
        resolved_path: str = "",
    ) -> None:
        super().__init__(message)
        self.user_id        = user_id
        self.requested_path = requested_path
        self.resolved_path  = resolved_path


class PathGuard:
    """
    Validazione e audit di tutti gli accessi al filesystem di Cipher.

    SECURITY-STEP2: centralizza path traversal check in un posto solo.
    Usa Path.resolve() + relative_to() — non str.startswith() — per
    gestire correttamente: '../', symlink, path assoluti, null byte.

    Due metodi principali:
      validate_path()         → file utente (home/user_<id>/)
      validate_project_path() → file di progetto (cipher-server/)
    """

    def __init__(
        self,
        home_root:   Path = Config.HOME_ROOT,
        project_root: Path = Config.BASE_DIR,
        audit_log:   Path = _AUDIT_LOG,
    ) -> None:
        # SECURITY-STEP2: home_root è il confine fisico per path traversal utente
        self.home_root    = home_root.resolve()
        self.project_root = project_root.resolve()
        self._audit_logger = self._setup_audit_logger(audit_log)

        # Applica 0o700 su home_root all'avvio
        self.home_root.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.home_root, 0o700)
        except OSError:
            pass  # non bloccare l'avvio per permessi

    # ── Setup logger ──────────────────────────────────────────────────────────

    @staticmethod
    def _setup_audit_logger(log_path: Path) -> logging.Logger:
        """
        SECURITY-STEP2: audit log persistente con rotazione automatica.
        5 MB per file × 10 file archiviati = max 50 MB totali.
        """
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger("cipher.file_audit")
        if not logger.handlers:
            handler = logging.handlers.RotatingFileHandler(
                str(log_path),
                maxBytes=5 * 1024 * 1024,
                backupCount=10,
                encoding="utf-8",
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.setLevel(logging.INFO)
            logger.propagate = False
            logger.addHandler(handler)
        return logger

    # ── API pubblica — file utente ────────────────────────────────────────────

    def get_user_home(self, user_id: str) -> Path:
        """
        Ritorna (e crea se necessario) la home directory per user_id.

        SECURITY-STEP2: valida user_id contro regex per impedire path
        injection tramite user_id stesso (es. user_id="../other").
        Crea la directory con permessi 0o700.

        Args:
            user_id: identificativo utente — deve matchare ^[a-z0-9_]{2,32}$

        Returns:
            Path assoluto a home/user_<user_id>/

        Raises:
            ValueError: user_id non valido
        """
        if not _USER_ID_RE.match(user_id):
            raise ValueError(
                f"user_id non valido: '{user_id}' "
                f"(accettato: lettere minuscole, cifre, underscore, 2-32 caratteri)"
            )
        user_home = self.home_root / f"user_{user_id}"
        user_home.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(user_home, 0o700)
        except OSError:
            pass
        return user_home

    def validate_path(
        self,
        user_id:        str,
        requested_path: str,
        operation:      str,
        size_bytes:     Optional[int] = None,
    ) -> Path:
        """
        Valida requested_path per user_id e ritorna il Path assoluto sicuro.

        SECURITY-STEP2: usa resolve() + relative_to() — non startswith() —
        per gestire correttamente tutti i vettori di path traversal:
          - '../../../etc/passwd'       → resolve, poi relative_to fallisce
          - symlink → /etc/passwd       → resolve segue il link, fallisce
          - '/etc/passwd'               → lstrip + resolve fuori da user_home
          - 'test\x00.txt' (null byte)  → check esplicito prima del parsing
          - '~/.bashrc'                 → resolve senza expanduser, fuori

        Args:
            user_id:        chi accede
            requested_path: path grezzo dalla richiesta (utente o LLM)
            operation:      READ | WRITE | LIST | DELETE | UPLOAD
            size_bytes:     dimensione file (per audit), opzionale

        Returns:
            Path assoluto validato dentro home/user_<user_id>/

        Raises:
            PathTraversalError: path fuori dalla user_home

        NOTA: questo metodo valida solo che il path sia dentro il recinto
        dell'utente. Non verifica che il file esista né che sia leggibile.
        Il chiamante deve gestire FileNotFoundError / PermissionError.
        """
        t_start = time.monotonic()

        # 1. Null byte — impedisce bypass su alcuni filesystem/OS
        if "\x00" in requested_path:
            reason = "null byte nel path"
            self._audit(
                user_id=user_id, source="user_fs", operation=operation,
                requested_path=requested_path, resolved_path=None,
                blocked=True, block_reason=reason, size_bytes=None,
            )
            raise PathTraversalError(reason, user_id, requested_path)

        # 2. Ottieni user_home (crea se non esiste, valida user_id)
        try:
            user_home = self.get_user_home(user_id).resolve()
        except ValueError as e:
            reason = str(e)
            self._audit(
                user_id=user_id, source="user_fs", operation=operation,
                requested_path=requested_path, resolved_path=None,
                blocked=True, block_reason=reason, size_bytes=None,
            )
            raise PathTraversalError(reason, user_id, requested_path)

        # 3. Tratta path assoluti come relativi (strip '/' iniziale)
        #    NON usare expanduser() — evita espansione di ~ verso /home/Szymon
        rel = requested_path.lstrip("/")

        # 4. Risolvi il path (segue symlink, normalizza ..)
        try:
            resolved = (user_home / rel).resolve()
        except Exception as e:
            reason = f"path non risolvibile: {e}"
            self._audit(
                user_id=user_id, source="user_fs", operation=operation,
                requested_path=requested_path, resolved_path=None,
                blocked=True, block_reason=reason, size_bytes=None,
            )
            raise PathTraversalError(reason, user_id, requested_path)

        # 5. Verifica containment — relative_to lancia ValueError se fuori
        #    Questo è il check principale: gestisce '..' E symlink in un colpo
        try:
            resolved.relative_to(user_home)
        except ValueError:
            reason = (
                f"accesso negato: '{requested_path}' "
                f"è fuori dalla home utente ({resolved})"
            )
            self._audit(
                user_id=user_id, source="user_fs", operation=operation,
                requested_path=requested_path, resolved_path=str(resolved),
                blocked=True, block_reason=reason, size_bytes=None,
            )
            raise PathTraversalError(reason, user_id, requested_path, str(resolved))

        # 6. Audit — operazione consentita
        self._audit(
            user_id=user_id, source="user_fs", operation=operation,
            requested_path=requested_path, resolved_path=str(resolved),
            blocked=False, block_reason=None, size_bytes=size_bytes,
        )
        return resolved

    # ── API pubblica — file di progetto ──────────────────────────────────────

    def validate_project_path(
        self,
        requested_path: str,
        operation:      str,
        size_bytes:     Optional[int] = None,
    ) -> Path:
        """
        Valida requested_path contro Config.BASE_DIR (root del progetto).

        SECURITY-STEP2: usato da filesystem.project_read/project_write.
        Stesso pattern di validate_path ma con project_root come confine.
        Registrato nel medesimo audit log con source="project_fs" per
        distinguere dagli accessi file utente.

        Args:
            requested_path: path grezzo (relativo alla root del progetto)
            operation:      READ | WRITE | LIST
            size_bytes:     dimensione file (per audit), opzionale

        Returns:
            Path assoluto validato dentro cipher-server/

        Raises:
            PathTraversalError: path fuori dalla project_root
        """
        # 1. Null byte
        if "\x00" in requested_path:
            reason = "null byte nel path"
            self._audit(
                user_id="system", source="project_fs", operation=operation,
                requested_path=requested_path, resolved_path=None,
                blocked=True, block_reason=reason, size_bytes=None,
            )
            raise PathTraversalError(reason, "system", requested_path)

        # 2. Tratta path assoluti come relativi
        rel = requested_path.lstrip("/")

        # 3. Risolvi
        try:
            resolved = (self.project_root / rel).resolve()
        except Exception as e:
            reason = f"path non risolvibile: {e}"
            self._audit(
                user_id="system", source="project_fs", operation=operation,
                requested_path=requested_path, resolved_path=None,
                blocked=True, block_reason=reason, size_bytes=None,
            )
            raise PathTraversalError(reason, "system", requested_path)

        # 4. Verifica containment
        try:
            resolved.relative_to(self.project_root)
        except ValueError:
            reason = (
                f"accesso negato: '{requested_path}' "
                f"è fuori dalla root del progetto ({resolved})"
            )
            self._audit(
                user_id="system", source="project_fs", operation=operation,
                requested_path=requested_path, resolved_path=str(resolved),
                blocked=True, block_reason=reason, size_bytes=None,
            )
            raise PathTraversalError(reason, "system", requested_path, str(resolved))

        # 5. Audit — consentito
        self._audit(
            user_id="system", source="project_fs", operation=operation,
            requested_path=requested_path, resolved_path=str(resolved),
            blocked=False, block_reason=None, size_bytes=size_bytes,
        )
        return resolved

    # ── Audit ─────────────────────────────────────────────────────────────────

    def _audit(
        self,
        *,
        user_id:        str,
        source:         str,
        operation:      str,
        requested_path: str,
        resolved_path:  Optional[str],
        blocked:        bool,
        block_reason:   Optional[str],
        size_bytes:     Optional[int],
    ) -> None:
        """
        Scrive un record JSON Lines nel log di audit file.

        SECURITY-STEP2: ogni accesso (consentito o bloccato) viene
        registrato. Predisposto per multi-utente (campo user_id).
        """
        record = {
            "ts":             datetime.now(timezone.utc).isoformat(),
            "user_id":        user_id,
            "source":         source,
            "operation":      operation,
            "requested_path": requested_path,
            "resolved_path":  resolved_path,
            "blocked":        blocked,
            "block_reason":   block_reason,
            "size_bytes":     size_bytes,
        }
        try:
            self._audit_logger.info(json.dumps(record, ensure_ascii=False))
        except Exception:
            pass  # il log non deve mai bloccare l'operazione


# ── Singleton globale ─────────────────────────────────────────────────────────
_guard: Optional[PathGuard] = None


def get_path_guard() -> PathGuard:
    """
    Ritorna (o crea) il singleton PathGuard condiviso da tutti i moduli.

    SECURITY-STEP2: singleton garantisce un unico audit logger e una
    singola istanza di PathGuard per tutta la vita del processo.
    """
    global _guard
    if _guard is None:
        _guard = PathGuard()
    return _guard


def get_user_home(user_id: str) -> Path:
    """
    Convenience function: ritorna la home directory per user_id.
    Delegato al singleton PathGuard.
    """
    return get_path_guard().get_user_home(user_id)
