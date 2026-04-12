"""
modules/filesystem.py – Gestione file e cartelle per Cipher
- Home (~/cipher/home/): lettura e scrittura libera
- Project (~/cipher/): lettura libera, scrittura solo con consenso esplicito
"""

import logging
import os
import shutil
from pathlib import Path

from config import Config

log = logging.getLogger("cipher.filesystem")

# Root utente — lettura e scrittura libera
HOME_ROOT = Config.HOME_DIR

# Root progetto — lettura libera, scrittura con consenso
PROJECT_ROOT = Config.BASE_DIR

# File e cartelle da nascondere nella root progetto (troppo sensibili)
PROJECT_HIDDEN = {".env", "venv", "__pycache__", ".git", "token.json", "credentials.json"}


def _safe_path(relative: str, root: Path) -> Path:
    # Path assoluti: tratta come relativi a root (es. /home → home/ dentro HOME_DIR)
    if relative.startswith("/"):
        relative = relative.lstrip("/")
    target = (root / relative).resolve()
    if not str(target).startswith(str(root.resolve())):
        raise ValueError(f"Accesso negato: '{relative}' è fuori dalla cartella consentita.")
    return target


class FileSystem:

    # ─── HOME (lettura + scrittura libera) ───────────────────────────────────

    def list_dir(self, path: str = "") -> str:
        try:
            target = _safe_path(path, HOME_ROOT)
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
        except ValueError as e:
            return str(e)
        except Exception as e:
            return f"Errore: {e}"

    def read_file(self, path: str) -> str:
        try:
            target = _safe_path(path, HOME_ROOT)
            if not target.exists():
                return f"Il file '{path}' non esiste."
            if not target.is_file():
                return f"'{path}' non è un file."
            if target.stat().st_size > 100_000:
                return f"Il file '{path}' è troppo grande (max 100KB)."
            return f"📄 home/{path}:\n\n{target.read_text(encoding='utf-8')}"
        except ValueError as e:
            return str(e)
        except UnicodeDecodeError:
            return f"Il file '{path}' non è leggibile come testo."
        except Exception as e:
            return f"Errore: {e}"

    def write_file(self, path: str, content: str, append: bool = False) -> str:
        try:
            target = _safe_path(path, HOME_ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if append else "w"
            with open(target, mode, encoding="utf-8") as f:
                f.write(content)
            action = "Aggiunto a" if append else "Creato"
            return f"✓ {action} 'home/{path}'."
        except ValueError as e:
            return str(e)
        except Exception as e:
            return f"Errore: {e}"

    def make_dir(self, path: str) -> str:
        try:
            target = _safe_path(path, HOME_ROOT)
            if target.exists():
                return f"La cartella '{path}' esiste già."
            target.mkdir(parents=True)
            return f"✓ Cartella 'home/{path}' creata."
        except ValueError as e:
            return str(e)
        except Exception as e:
            return f"Errore: {e}"

    def delete(self, path: str) -> str:
        try:
            target = _safe_path(path, HOME_ROOT)
            if not target.exists():
                return f"'{path}' non esiste."
            if target.is_dir():
                shutil.rmtree(target)
                return f"✓ Cartella 'home/{path}' eliminata."
            else:
                target.unlink()
                return f"✓ File 'home/{path}' eliminato."
        except ValueError as e:
            return str(e)
        except Exception as e:
            return f"Errore: {e}"

    def move(self, src: str, dst: str) -> str:
        try:
            src_path = _safe_path(src, HOME_ROOT)
            dst_path = _safe_path(dst, HOME_ROOT)
            if not src_path.exists():
                return f"'{src}' non esiste."
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src_path), str(dst_path))
            return f"✓ 'home/{src}' spostato in 'home/{dst}'."
        except ValueError as e:
            return str(e)
        except Exception as e:
            return f"Errore: {e}"

    # ─── PROJECT (lettura libera) ─────────────────────────────────────────────

    def project_list(self, path: str = "") -> str:
        try:
            target = _safe_path(path, PROJECT_ROOT)
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
        except ValueError as e:
            return str(e)
        except Exception as e:
            return f"Errore: {e}"

    def project_read(self, path: str) -> str:
        try:
            target = _safe_path(path, PROJECT_ROOT)
            # Blocca file sensibili
            if target.name in PROJECT_HIDDEN:
                return f"Accesso negato: '{path}' è un file protetto."
            if not target.exists():
                return f"Il file '{path}' non esiste nel progetto."
            if not target.is_file():
                return f"'{path}' non è un file."
            if target.stat().st_size > 100_000:
                return f"Il file '{path}' è troppo grande (max 100KB)."
            return f"📄 cipher/{path}:\n\n{target.read_text(encoding='utf-8')}"
        except ValueError as e:
            return str(e)
        except UnicodeDecodeError:
            return f"Il file '{path}' non è leggibile come testo."
        except Exception as e:
            return f"Errore: {e}"

    def project_write(self, path: str, content: str, append: bool = False) -> str:
        """
        Scrittura sul progetto. Viene chiamata SOLO dopo consenso esplicito.
        Il controllo del consenso avviene nel dispatcher/brain, non qui.
        """
        try:
            target = _safe_path(path, PROJECT_ROOT)
            if target.name in PROJECT_HIDDEN:
                return f"Accesso negato: '{path}' è un file protetto."
            target.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if append else "w"
            with open(target, mode, encoding="utf-8") as f:
                f.write(content)
            action = "Aggiunto a" if append else "Sovrascritto"
            log.warning("project_write: %s %s", action, target)
            return f"✓ {action} 'cipher/{path}'."
        except ValueError as e:
            return str(e)
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
