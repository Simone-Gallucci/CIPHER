"""
modules/filesystem.py – Gestione file e cartelle per Cipher

SECURITY-STEP2: _safe_path() rimossa (usava str.startswith() bypassabile).
Tutta la validazione path delegata a PathGuard.validate_path() e
PathGuard.validate_project_path(). FileSystem accetta user_id nel costruttore.

- Home utente (home/user_<id>/): lettura e scrittura libera
- Project (cipher-server/):      lettura libera, scrittura solo con consenso
"""

import logging
import shutil
from pathlib import Path
from typing import Optional

from config import Config

log = logging.getLogger("cipher.filesystem")

# File e cartelle da nascondere nella root progetto (troppo sensibili)
PROJECT_HIDDEN = {".env", "venv", "__pycache__", ".git", "token.json", "credentials.json"}


class FileSystem:

    def __init__(self, user_id: Optional[str] = None) -> None:
        """
        SECURITY-STEP2: user_id determina la home utente via PathGuard.
        Default a get_current_user_id() se non specificato.
        """
        from modules.auth import get_current_user_id
        self.user_id = user_id or get_current_user_id()

    def _guard(self):
        """Ritorna il singleton PathGuard."""
        from modules.path_guard import get_path_guard
        return get_path_guard()

    # ─── HOME (lettura + scrittura libera) ───────────────────────────────────

    def list_dir(self, path: str = "") -> str:
        # SECURITY-STEP2: validate_path gestisce traversal, symlink, null byte
        from modules.path_guard import PathTraversalError
        try:
            target = self._guard().validate_path(self.user_id, path or ".", "LIST")
        except PathTraversalError as e:
            return str(e)
        if not target.exists():
            return f"La cartella '{path or '/'}' non esiste."
        if not target.is_dir():
            return f"'{path}' non è una cartella."
        items = sorted(target.iterdir(), key=lambda x: (x.is_file(), x.name))
        if not items:
            return f"La cartella '{path or '/'}' è vuota."
        lines = [f"📁 Contenuto di home/{path}:"]
        for item in items:
            if item.is_dir():
                lines.append(f"  📂 {item.name}/")
            else:
                lines.append(f"  📄 {item.name} ({self._human_size(item.stat().st_size)})")
        return "\n".join(lines)

    def read_file(self, path: str) -> str:
        from modules.path_guard import PathTraversalError
        try:
            target = self._guard().validate_path(self.user_id, path, "READ")
        except PathTraversalError as e:
            return str(e)
        if not target.exists():
            return f"Il file '{path}' non esiste."
        if not target.is_file():
            return f"'{path}' non è un file."
        if target.stat().st_size > 100_000:
            return f"Il file '{path}' è troppo grande (max 100KB)."
        try:
            return f"📄 home/{path}:\n\n{target.read_text(encoding='utf-8')}"
        except UnicodeDecodeError:
            return f"Il file '{path}' non è leggibile come testo."
        except Exception as e:
            return f"Errore: {e}"

    def write_file(self, path: str, content: str, append: bool = False) -> str:
        from modules.path_guard import PathTraversalError
        try:
            target = self._guard().validate_path(self.user_id, path, "WRITE")
        except PathTraversalError as e:
            return str(e)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if append else "w"
            with open(target, mode, encoding="utf-8") as f:
                f.write(content)
            action = "Aggiunto a" if append else "Creato"
            return f"✓ {action} 'home/{path}'."
        except Exception as e:
            return f"Errore: {e}"

    def make_dir(self, path: str) -> str:
        from modules.path_guard import PathTraversalError
        try:
            target = self._guard().validate_path(self.user_id, path, "WRITE")
        except PathTraversalError as e:
            return str(e)
        if target.exists():
            return f"La cartella '{path}' esiste già."
        try:
            target.mkdir(parents=True)
            return f"✓ Cartella 'home/{path}' creata."
        except Exception as e:
            return f"Errore: {e}"

    def delete(self, path: str) -> str:
        from modules.path_guard import PathTraversalError
        try:
            target = self._guard().validate_path(self.user_id, path, "DELETE")
        except PathTraversalError as e:
            return str(e)
        if not target.exists():
            return f"'{path}' non esiste."
        try:
            if target.is_dir():
                shutil.rmtree(target)
                return f"✓ Cartella 'home/{path}' eliminata."
            else:
                target.unlink()
                return f"✓ File 'home/{path}' eliminato."
        except Exception as e:
            return f"Errore: {e}"

    def move(self, src: str, dst: str) -> str:
        from modules.path_guard import PathTraversalError
        try:
            src_path = self._guard().validate_path(self.user_id, src, "READ")
            dst_path = self._guard().validate_path(self.user_id, dst, "WRITE")
        except PathTraversalError as e:
            return str(e)
        if not src_path.exists():
            return f"'{src}' non esiste."
        try:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src_path), str(dst_path))
            return f"✓ 'home/{src}' spostato in 'home/{dst}'."
        except Exception as e:
            return f"Errore: {e}"

    # ─── PROJECT (lettura libera, scrittura con consenso) ─────────────────────

    def project_list(self, path: str = "") -> str:
        # SECURITY-STEP2: validate_project_path al posto di _safe_path(path, PROJECT_ROOT)
        from modules.path_guard import PathTraversalError
        try:
            target = self._guard().validate_project_path(path or ".", "LIST")
        except PathTraversalError as e:
            return str(e)
        if not target.exists():
            return f"'{path or '/'}' non esiste nel progetto."
        if not target.is_dir():
            return f"'{path}' non è una cartella."
        items = sorted(target.iterdir(), key=lambda x: (x.is_file(), x.name))
        items = [i for i in items if i.name not in PROJECT_HIDDEN]
        if not items:
            return f"La cartella 'cipher/{path}' è vuota."
        lines = [f"📁 cipher/{path}:"]
        for item in items:
            if item.is_dir():
                lines.append(f"  📂 {item.name}/")
            else:
                lines.append(f"  📄 {item.name} ({self._human_size(item.stat().st_size)})")
        return "\n".join(lines)

    def project_read(self, path: str) -> str:
        from modules.path_guard import PathTraversalError
        try:
            target = self._guard().validate_project_path(path, "READ")
        except PathTraversalError as e:
            return str(e)
        if target.name in PROJECT_HIDDEN:
            return f"Accesso negato: '{path}' è un file protetto."
        if not target.exists():
            return f"Il file '{path}' non esiste nel progetto."
        if not target.is_file():
            return f"'{path}' non è un file."
        if target.stat().st_size > 100_000:
            return f"Il file '{path}' è troppo grande (max 100KB)."
        try:
            return f"📄 cipher/{path}:\n\n{target.read_text(encoding='utf-8')}"
        except UnicodeDecodeError:
            return f"Il file '{path}' non è leggibile come testo."
        except Exception as e:
            return f"Errore: {e}"

    def project_write(self, path: str, content: str, append: bool = False) -> str:
        """
        Scrittura sul progetto. Viene chiamata SOLO dopo consenso esplicito.
        Il controllo del consenso avviene nel dispatcher/brain, non qui.
        Se il file esiste e non è append, crea prima un .bak e registra in changelog.

        NOTA: in modalità append=True NON viene creato alcun backup .bak.
        Usare solo per file log o aggiunte incrementali dove rollback non
        serve. Per modifiche che sovrascrivono contenuto esistente, usare
        append=False che crea sempre .bak prima della scrittura.
        """
        from modules.path_guard import PathTraversalError
        try:
            target = self._guard().validate_project_path(path, "WRITE")
        except PathTraversalError as e:
            return str(e)
        if target.name in PROJECT_HIDDEN:
            return f"Accesso negato: '{path}' è un file protetto."
        try:
            target.parent.mkdir(parents=True, exist_ok=True)

            # Crea .bak PRIMA di sovrascrivere — snapshot dello stato precedente
            if not append and target.exists():
                backup_path = target.with_suffix(target.suffix + ".bak")
                import shutil as _shutil
                _shutil.copy2(target, backup_path)
                log.info("project_write: backup creato %s", backup_path)
                try:
                    from modules.admin_manager import log_backup
                    log_backup(target, backup_path)
                except Exception as _bak_err:
                    log.warning("log_backup fallito: %s", _bak_err)

            mode = "a" if append else "w"
            with open(target, mode, encoding="utf-8") as f:
                f.write(content)
            action = "Aggiunto a" if append else "Sovrascritto"
            log.warning("project_write: %s %s", action, target)
            return f"✓ {action} 'cipher/{path}'."
        except Exception as e:
            return f"Errore: {e}"

    # ─── Utility ─────────────────────────────────────────────────────────────

    @staticmethod
    def _human_size(size: int) -> str:
        for unit in ("B", "KB", "MB"):
            if size < 1024:
                return f"{size:.0f} {unit}"
            size /= 1024
        return f"{size:.1f} GB"
