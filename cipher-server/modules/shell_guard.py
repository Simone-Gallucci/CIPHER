"""
modules/shell_guard.py – Esecuzione sicura di comandi shell

SECURITY-STEP1: Sostituisce subprocess(shell=True) con esecuzione argv-list
validata tramite whitelist, blocco pattern pericolosi, path traversal check,
env pulito e audit log persistente.

Usato da:
  server.py   → ShellGuard.validate_and_run_terminal()   (endpoint /api/terminal)
  actions.py  → ShellGuard.validate_and_run_shell_exec() (shell_exec via Telegram)
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
import shlex
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import Config

# ── Percorsi ─────────────────────────────────────────────────────────────────
# SECURITY-STEP1: log separato da MEMORY_DIR → non resettato da Tabula Rasa
_LOGS_DIR  = Config.BASE_DIR / "logs"
_AUDIT_LOG = _LOGS_DIR / "shell_audit.log"

# ── Limiti globali ────────────────────────────────────────────────────────────
MAX_CMD_LENGTH       = 500      # caratteri massimi del comando grezzo
TERMINAL_TIMEOUT     = 10       # secondi, terminale web
SHELL_EXEC_TIMEOUT   = 30       # secondi, shell_exec Telegram
TERMINAL_MAX_OUTPUT  = 51_200   # 50 KB
SHELL_EXEC_MAX_OUT   = 10_240   # 10 KB
JOURNALCTL_MAX_LINES = 500

# ── Whitelist terminale web (dashboard — utente fisicamente presente) ─────────
# SECURITY-STEP1: whitelist esplicita invece di blocklist bypassabile.
# Esclusi deliberatamente:
#   bash/sh/python*/python → -c "..." aggira qualsiasi validazione token
#   nano/less/more         → editor/pager interattivi, inutili senza TTY
#   env/printenv           → superficie d'attacco inutile; se in futuro una
#                            variabile viene aggiunta a _clean_env per sbaglio,
#                            printenv la espone
#   tar/gzip/gunzip/zip/unzip → flag pericolosi (--to-command, -C, zip-slip)
#                               che richiederebbero validazione separata
TERMINAL_ALLOWED_BINS: frozenset[str] = frozenset({
    "ls", "ll", "la",
    "cat", "head", "tail",
    "grep", "find", "du", "df", "stat",
    "wc", "sort", "uniq",
    "pwd", "echo", "date", "whoami",
    "touch", "mkdir", "rm", "mv", "cp", "chmod",
    "file", "diff",
    "which", "type", "hash",
})

# ── Whitelist shell_exec Telegram (più stretta — input da LLM) ───────────────
# SECURITY-STEP1: nessuna operazione di scrittura filesystem; solo read-only
# e strumenti diagnostici. Subcomandi limitati per git/pip/systemctl.
SHELL_EXEC_ALLOWED_BINS: frozenset[str] = frozenset({
    "ls", "ll", "la",
    "cat", "head", "tail",
    "grep", "find", "du", "df", "stat",
    "wc", "sort", "uniq",
    "pwd", "echo", "date", "whoami",
    "file", "diff",
    "which", "type", "hash",
    "ps", "top", "free", "uptime",
    "git",         # solo subcomandi read-only, vedi RESTRICTED_SUBCOMMANDS
    "pip", "pip3", # solo list/show/freeze
    "systemctl",   # solo status/is-active/list-units
    "journalctl",  # --lines forzato ≤ JOURNALCTL_MAX_LINES
})

# ── Subcomandi ristretti ──────────────────────────────────────────────────────
# SECURITY-STEP1: per i binari con frozenset non vuoto il secondo token
# deve essere uno dei subcomandi read-only elencati.
RESTRICTED_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "git":       frozenset({"status", "log", "diff", "show", "branch"}),
    "pip":       frozenset({"list", "show", "freeze"}),
    "pip3":      frozenset({"list", "show", "freeze"}),
    "systemctl": frozenset({"status", "is-active", "list-units"}),
    # journalctl: nessun subcomando obbligatorio, gestito via _enforce_journalctl_lines
}

# ── Flag pericolosi per specifici binari ──────────────────────────────────────
# SECURITY-STEP1: find -exec/-execdir permette esecuzione arbitraria di comandi.
DANGEROUS_FLAGS: dict[str, frozenset[str]] = {
    "find": frozenset({"-exec", "-execdir", "-ok", "-okdir", "-delete"}),
}

# ── Opzioni di find che non sono path root ma consumano un valore ────────────
# SECURITY-STEP1: dopo questi flag il token successivo è un pattern/valore,
# non un path da validare → evita falsi positivi nel path-traversal check.
_FIND_VALUE_FLAGS: frozenset[str] = frozenset({
    "-name", "-iname", "-regex", "-iregex",
    "-path", "-ipath", "-wholename", "-iwholename",
    "-size", "-newer", "-newermt", "-newerat", "-newerct",
    "-maxdepth", "-mindepth",
    "-mtime", "-atime", "-ctime", "-mmin", "-amin", "-cmin",
    "-perm", "-user", "-group", "-uid", "-gid",
    "-type", "-xtype",
    "-printf", "-fprintf",
})

# ── Opzioni globali di find (prima del path, non consumano valore) ────────────
_FIND_GLOBAL_OPTS: frozenset[str] = frozenset({"-H", "-L", "-P"})

# ── Pattern vietati nel testo grezzo del comando ──────────────────────────────
# SECURITY-STEP1: controllo sul raw string prima di qualsiasi parsing.
# Blocca: ; & ` < > singoli, || $() ${} newline.
# Permette: | singolo → separatore di pipe, gestito separatamente.
_FORBIDDEN_RAW = re.compile(
    r'[;&`<>]'    # separatori shell, redirect, backtick
    r'|\|\|'      # or-chain (||)
    r'|\$[\(\{]'  # command substitution $() o ${}
    r'|[\n\r]'    # newline injection
)


class ShellGuard:
    """
    Validazione e esecuzione sicura di comandi shell per Cipher.

    SECURITY-STEP1: centralizza tutta la logica di esecuzione shell per
    eliminare i due punti critici con shell=True (server.py:791,
    actions.py:157). Ogni esecuzione viene registrata nel log di audit.
    """

    def __init__(
        self,
        home_dir: Optional[Path] = None,
        audit_log: Path = _AUDIT_LOG,
    ) -> None:
        # SECURITY-STEP2: home_dir ora è per-utente via get_user_home().
        # Default a get_current_user_id() se non specificato esplicitamente.
        # SECURITY-STEP1: home_dir è il confine fisico per path traversal
        if home_dir is None:
            from modules.auth import get_current_user_id
            from modules.path_guard import get_user_home
            home_dir = get_user_home(get_current_user_id())
        self.home_dir = home_dir.resolve()
        self._audit_logger = self._setup_audit_logger(audit_log)

    # ── Setup logger ──────────────────────────────────────────────────────────

    @staticmethod
    def _setup_audit_logger(log_path: Path) -> logging.Logger:
        """
        SECURITY-STEP1: log di audit persistente con rotazione automatica.
        5 MB per file × 10 file archiviati = max 50 MB totali.
        """
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger("cipher.shell_audit")
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

    # ── API pubblica ──────────────────────────────────────────────────────────

    def validate_and_run_terminal(
        self,
        cmd: str,
        cwd: Path,
        user_id: str = "simone",
    ) -> dict:
        """
        Valida ed esegue un comando dal terminale web (/api/terminal).

        SECURITY-STEP1: sostituisce subprocess.run(cmd, shell=True, env={**os.environ}).
        Usa argv-list (no shell expansion), whitelist binari, blocco pattern
        pericolosi, path traversal check, env pulito senza secret.

        Supporta pipe (ls | grep x) ma non redirect, chaining o substitution.

        Args:
            cmd:     Comando grezzo dalla dashboard web.
            cwd:     Working directory corrente (deve essere dentro HOME_DIR).
            user_id: Identificativo utente per il log di audit.

        Returns:
            dict: output (str), exit_code (int), blocked (bool), block_reason (str|None).
        """
        cmd_raw = cmd
        t_start = time.monotonic()

        # 1. Lunghezza massima
        if len(cmd) > MAX_CMD_LENGTH:
            reason = f"comando troppo lungo ({len(cmd)} > {MAX_CMD_LENGTH} caratteri)"
            self._audit(user_id=user_id, source="web_terminal", cmd_raw=cmd_raw,
                        cmd_executed=None, blocked=True, block_reason=reason,
                        exit_code=None, duration_ms=None, stdout_preview=None)
            return {"output": "", "exit_code": 1, "blocked": True, "block_reason": reason}

        # 2. Pattern vietati nel raw string (prima di qualsiasi parsing)
        m = _FORBIDDEN_RAW.search(cmd)
        if m:
            reason = f"pattern non consentito: '{m.group()}'"
            self._audit(user_id=user_id, source="web_terminal", cmd_raw=cmd_raw,
                        cmd_executed=None, blocked=True, block_reason=reason,
                        exit_code=None, duration_ms=None, stdout_preview=None)
            return {"output": "", "exit_code": 1, "blocked": True, "block_reason": reason}

        # 3. Parsing pipeline
        try:
            segments = self._parse_pipeline(cmd)
        except ValueError as e:
            reason = f"errore di parsing: {e}"
            self._audit(user_id=user_id, source="web_terminal", cmd_raw=cmd_raw,
                        cmd_executed=None, blocked=True, block_reason=reason,
                        exit_code=None, duration_ms=None, stdout_preview=None)
            return {"output": "", "exit_code": 1, "blocked": True, "block_reason": reason}

        # 4. Validazione di ogni segmento
        final_segments: list[list[str]] = []
        for argv in segments:
            ok, reason, final_argv = self._validate_single_command(
                argv, cwd, TERMINAL_ALLOWED_BINS
            )
            if not ok:
                self._audit(user_id=user_id, source="web_terminal", cmd_raw=cmd_raw,
                            cmd_executed=None, blocked=True, block_reason=reason,
                            exit_code=None, duration_ms=None, stdout_preview=None)
                return {"output": "", "exit_code": 1, "blocked": True, "block_reason": reason}
            final_segments.append(final_argv)

        # 5. Esecuzione
        cmd_executed = " | ".join(" ".join(seg) for seg in final_segments)
        try:
            output, exit_code = self._execute_pipeline(
                final_segments, cwd, TERMINAL_TIMEOUT, TERMINAL_MAX_OUTPUT
            )
        except Exception as e:
            duration_ms = (time.monotonic() - t_start) * 1000
            self._audit(user_id=user_id, source="web_terminal", cmd_raw=cmd_raw,
                        cmd_executed=cmd_executed, blocked=False, block_reason=None,
                        exit_code=1, duration_ms=duration_ms, stdout_preview=None)
            return {"output": f"✗ Errore: {e}", "exit_code": 1,
                    "blocked": False, "block_reason": None}

        duration_ms = (time.monotonic() - t_start) * 1000
        self._audit(user_id=user_id, source="web_terminal", cmd_raw=cmd_raw,
                    cmd_executed=cmd_executed, blocked=False, block_reason=None,
                    exit_code=exit_code, duration_ms=duration_ms,
                    stdout_preview=output[:500])
        return {"output": output, "exit_code": exit_code,
                "blocked": False, "block_reason": None}

    def validate_and_run_shell_exec(
        self,
        command: str,
        timeout: int = SHELL_EXEC_TIMEOUT,
        user_id: str = "simone",
    ) -> str:
        """
        Valida ed esegue un comando shell_exec da Telegram/dispatcher.

        SECURITY-STEP1: sostituisce subprocess.run(command, shell=True, ...).
        Il consenso è già stato ottenuto da ActionDispatcher prima di chiamare
        questo metodo. Esegue in HOME_DIR con env pulito senza secret.
        Non supporta pipe (shell Telegram è single-command).

        Args:
            command: Comando grezzo da LLM/utente Telegram.
            timeout: Timeout in secondi (cappato a SHELL_EXEC_TIMEOUT).
            user_id: Identificativo utente per il log di audit.

        Returns:
            Stringa di output da restituire via Telegram.
        """
        cmd_raw = command
        t_start = time.monotonic()
        cwd = self.home_dir
        effective_timeout = min(timeout, SHELL_EXEC_TIMEOUT)

        # 1. Lunghezza massima
        if len(command) > MAX_CMD_LENGTH:
            reason = f"comando troppo lungo ({len(command)} > {MAX_CMD_LENGTH} caratteri)"
            self._audit(user_id=user_id, source="telegram_shell_exec", cmd_raw=cmd_raw,
                        cmd_executed=None, blocked=True, block_reason=reason,
                        exit_code=None, duration_ms=None, stdout_preview=None)
            return f"✗ Comando bloccato: {reason}"

        # 2. Pattern vietati nel raw string
        m = _FORBIDDEN_RAW.search(command)
        if m:
            reason = f"pattern non consentito: '{m.group()}'"
            self._audit(user_id=user_id, source="telegram_shell_exec", cmd_raw=cmd_raw,
                        cmd_executed=None, blocked=True, block_reason=reason,
                        exit_code=None, duration_ms=None, stdout_preview=None)
            return f"✗ Comando bloccato: {reason}"

        # 3. Parsing (nessuna pipe per shell_exec)
        try:
            argv = shlex.split(command)
        except ValueError as e:
            reason = f"errore di parsing: {e}"
            self._audit(user_id=user_id, source="telegram_shell_exec", cmd_raw=cmd_raw,
                        cmd_executed=None, blocked=True, block_reason=reason,
                        exit_code=None, duration_ms=None, stdout_preview=None)
            return f"✗ Comando bloccato: {reason}"

        if not argv:
            return "✗ Comando vuoto."

        # 4. Validazione
        ok, reason, final_argv = self._validate_single_command(
            argv, cwd, SHELL_EXEC_ALLOWED_BINS
        )
        if not ok:
            self._audit(user_id=user_id, source="telegram_shell_exec", cmd_raw=cmd_raw,
                        cmd_executed=None, blocked=True, block_reason=reason,
                        exit_code=None, duration_ms=None, stdout_preview=None)
            return f"✗ Comando bloccato: {reason}"

        # 5. Esecuzione
        cmd_executed = " ".join(final_argv)
        try:
            result = subprocess.run(
                final_argv,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                cwd=str(cwd),
                env=self._clean_env(cwd),
            )
        except subprocess.TimeoutExpired:
            duration_ms = (time.monotonic() - t_start) * 1000
            self._audit(user_id=user_id, source="telegram_shell_exec", cmd_raw=cmd_raw,
                        cmd_executed=cmd_executed, blocked=False, block_reason=None,
                        exit_code=124, duration_ms=duration_ms, stdout_preview=None)
            return f"✗ Timeout: il comando ha superato {effective_timeout} secondi."
        except Exception as e:
            duration_ms = (time.monotonic() - t_start) * 1000
            self._audit(user_id=user_id, source="telegram_shell_exec", cmd_raw=cmd_raw,
                        cmd_executed=cmd_executed, blocked=False, block_reason=None,
                        exit_code=1, duration_ms=duration_ms, stdout_preview=None)
            return f"✗ Errore durante l'esecuzione: {e}"

        duration_ms = (time.monotonic() - t_start) * 1000
        stdout_out = result.stdout.strip()
        stderr_out = result.stderr.strip()
        preview = (stdout_out + stderr_out)[:500]
        self._audit(user_id=user_id, source="telegram_shell_exec", cmd_raw=cmd_raw,
                    cmd_executed=cmd_executed, blocked=False, block_reason=None,
                    exit_code=result.returncode, duration_ms=duration_ms,
                    stdout_preview=preview)

        if result.returncode == 0:
            combined = stdout_out + ("\n" + stderr_out if stderr_out else "")
            return combined[:SHELL_EXEC_MAX_OUT] if combined else "✓ Comando eseguito senza output."

        msg = f"✗ Errore (exit code {result.returncode})"
        if stderr_out:
            msg += f":\n{stderr_out}"
        if stdout_out:
            msg += f"\nOutput:\n{stdout_out}"
        return msg[:SHELL_EXEC_MAX_OUT]

    # ── Metodi interni ────────────────────────────────────────────────────────

    def _parse_pipeline(self, cmd: str) -> list[list[str]]:
        """
        Tokenizza il comando con shlex e suddivide in segmenti di pipeline.

        SECURITY-STEP1: il '|' standalone è l'unico separatore consentito.
        '|' all'interno di stringhe quotate ("a|b") rimane parte del token.

        Returns:
            Lista di argv list, uno per ogni segmento.

        Raises:
            ValueError: parsing fallito o struttura invalida.
        """
        try:
            tokens = shlex.split(cmd)
        except ValueError as e:
            raise ValueError(str(e)) from e

        segments: list[list[str]] = []
        current: list[str] = []
        for token in tokens:
            if token == "|":
                if not current:
                    raise ValueError("pipe senza comando precedente")
                segments.append(current)
                current = []
            else:
                current.append(token)

        if not current:
            raise ValueError("pipe senza comando successivo" if segments else "comando vuoto")
        segments.append(current)
        return segments

    def _validate_single_command(
        self,
        argv: list[str],
        cwd: Path,
        allowed_bins: frozenset[str],
    ) -> tuple[bool, str, list[str]]:
        """
        Valida un singolo comando (argv list) contro le policy di sicurezza.

        Controlli in ordine:
          1. Whitelist binari
          2. Restrizione subcomandi (git, pip, systemctl)
          3. Cap --lines per journalctl
          4. Flag pericolosi (find -exec)
          5. Path traversal (generale o specifico per find)

        Returns:
            (ok, error_reason, final_argv)
            final_argv può differire da argv (es. journalctl con --lines cap).
        """
        if not argv:
            return False, "segmento vuoto", []

        bin_name = argv[0]

        # 1. Whitelist
        if bin_name not in allowed_bins:
            return False, f"binario non consentito: '{bin_name}'", []

        # 2. Subcomandi ristretti
        if bin_name in RESTRICTED_SUBCOMMANDS:
            allowed_subs = RESTRICTED_SUBCOMMANDS[bin_name]
            if allowed_subs:
                if len(argv) < 2 or argv[1].startswith("-"):
                    return (
                        False,
                        f"'{bin_name}' richiede un subcomando "
                        f"({', '.join(sorted(allowed_subs))})",
                        [],
                    )
                subcmd = argv[1]
                if subcmd not in allowed_subs:
                    return (
                        False,
                        f"'{bin_name} {subcmd}' non consentito "
                        f"(subcomandi validi: {', '.join(sorted(allowed_subs))})",
                        [],
                    )

        # 3. journalctl: forza --lines ≤ JOURNALCTL_MAX_LINES
        final_argv = argv if bin_name != "journalctl" else self._enforce_journalctl_lines(argv)

        # 4. Flag pericolosi
        if bin_name in DANGEROUS_FLAGS:
            for arg in final_argv[1:]:
                if arg in DANGEROUS_FLAGS[bin_name]:
                    return False, f"flag '{arg}' non consentito per '{bin_name}'", []

        # 5. Path traversal
        # SECURITY-STEP1: find ha validazione speciale (solo root path, non pattern)
        if bin_name == "find":
            ok, reason = self._validate_find_paths(final_argv[1:], cwd)
        else:
            ok, reason = self._validate_paths(final_argv[1:], cwd)

        if not ok:
            return False, reason, []

        return True, "", final_argv

    def _validate_paths(self, args: list[str], cwd: Path) -> tuple[bool, str]:
        """
        Verifica che tutti gli argomenti non-flag si risolvano dentro HOME_DIR.

        SECURITY-STEP1: blocca path traversal (../../) e path assoluti fuori
        da HOME_DIR (/etc/passwd). Gli argomenti che iniziano con '-' sono
        trattati come flag e ignorati.
        """
        home_str = str(self.home_dir)
        for arg in args:
            if arg.startswith("-"):
                continue  # flag, non è un path
            try:
                resolved = (cwd / arg).resolve()
            except Exception:
                continue
            resolved_str = str(resolved)
            if resolved_str != home_str and not resolved_str.startswith(home_str + os.sep):
                return False, f"accesso negato: '{arg}' è fuori da home/ ({resolved})"
        return True, ""

    def _validate_find_paths(self, args: list[str], cwd: Path) -> tuple[bool, str]:
        """
        Validazione path specializzata per il comando 'find'.

        SECURITY-STEP1: 'find' distingue tra path root (da validare) e argomenti
        valore dei flag come -name/-regex/-size (pattern/valori, da non validare).

        Logica:
          1. Salta opzioni globali (-H, -L, -P) che non consumano valore
          2. Raccoglie i path root: token consecutivi non-flag prima della prima
             espressione (qualsiasi token che inizia con '-')
          3. Valida solo i path root raccolti contro HOME_DIR
          4. Il check su -exec/-delete è già gestito da DANGEROUS_FLAGS
        """
        i = 0
        # Skip opzioni globali (non consumano un valore)
        while i < len(args) and args[i] in _FIND_GLOBAL_OPTS:
            i += 1
        # Raccogli path root: tutto prima della prima opzione/espressione (-)
        path_roots: list[str] = []
        while i < len(args):
            if args[i].startswith("-"):
                break
            path_roots.append(args[i])
            i += 1
        # Se nessun path root esplicito, il default è "." (cwd = HOME_DIR) → sicuro
        if not path_roots:
            return True, ""
        return self._validate_paths(path_roots, cwd)

    def _enforce_journalctl_lines(self, argv: list[str]) -> list[str]:
        """
        Rimuove flag -n/--lines esistenti e ne aggiunge uno cappato a
        JOURNALCTL_MAX_LINES. Default se non specificato: 100 righe.
        """
        new_argv = [argv[0]]
        requested = 100
        i = 1
        while i < len(argv):
            arg = argv[i]
            # Forma: -n VALUE (due token)
            if arg == "-n" and i + 1 < len(argv):
                try:
                    requested = min(int(argv[i + 1]), JOURNALCTL_MAX_LINES)
                except (ValueError, IndexError):
                    pass
                i += 2
                continue
            # Forma: -n100 (un token compatto)
            if re.match(r"^-n\d+$", arg):
                try:
                    requested = min(int(arg[2:]), JOURNALCTL_MAX_LINES)
                except ValueError:
                    pass
                i += 1
                continue
            # Forma: --lines VALUE (due token)
            if arg == "--lines" and i + 1 < len(argv):
                try:
                    requested = min(int(argv[i + 1]), JOURNALCTL_MAX_LINES)
                except (ValueError, IndexError):
                    pass
                i += 2
                continue
            # Forma: --lines=VALUE (un token)
            if arg.startswith("--lines="):
                try:
                    requested = min(int(arg[8:]), JOURNALCTL_MAX_LINES)
                except ValueError:
                    pass
                i += 1
                continue
            new_argv.append(arg)
            i += 1
        new_argv.append(f"--lines={requested}")
        return new_argv

    def _execute_pipeline(
        self,
        segments: list[list[str]],
        cwd: Path,
        timeout: int,
        max_output: int,
    ) -> tuple[str, int]:
        """
        Esegue una pipeline di comandi senza shell=True.

        SECURITY-STEP1: usa subprocess.Popen con argv-list e stdin/stdout
        collegati tramite PIPE. Nessuna interpretazione shell.

        Lo stderr degli stadi intermedi viene scartato (DEVNULL) per prevenire
        deadlock da buffer pieno. Lo stderr dell'ultimo stadio viene catturato.

        Returns:
            (output_string, exit_code_dell_ultimo_processo)
        """
        procs: list[subprocess.Popen] = []
        clean_env = self._clean_env(cwd)

        try:
            for i, argv in enumerate(segments):
                is_last = (i == len(segments) - 1)
                proc = subprocess.Popen(
                    argv,
                    stdin=procs[-1].stdout if procs else subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE if is_last else subprocess.DEVNULL,
                    cwd=str(cwd),
                    env=clean_env,
                )
                # SECURITY-STEP1: chiude stdout del processo precedente in questo
                # processo → consente a quello di ricevere SIGPIPE quando il
                # successivo termina, evitando zombie e deadlock.
                if procs:
                    procs[-1].stdout.close()  # type: ignore[union-attr]
                procs.append(proc)

            last = procs[-1]
            try:
                stdout_data, stderr_data = last.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                for p in procs:
                    p.kill()
                for p in procs:
                    p.wait()
                return "✗ Timeout: il comando ha superato il limite.", 124

            for p in procs[:-1]:
                p.wait()

            output = stdout_data.decode("utf-8", errors="replace")
            if stderr_data and stderr_data.strip():
                output += ("\n" if output.strip() else "") + \
                          stderr_data.decode("utf-8", errors="replace").strip()

            return output[:max_output], last.returncode

        except Exception:
            for p in procs:
                try:
                    p.kill()
                except Exception:
                    pass
            raise

    def _clean_env(self, cwd: Path) -> dict[str, str]:
        """
        Ritorna un ambiente minimale per i sottoprocessi.

        SECURITY-STEP1: whitelist da {} vuoto — NON os.environ.copy().
        Nessuna API key, token o secret del .env viene propagata.
        LANG/USER/LOGNAME hardcoded (non da os.environ) per difesa in profondità:
        anche se in futuro una variabile sensibile venisse aggiunta a os.environ
        con un nome comune, non sarebbe propagata.
        """
        return {
            "HOME":    str(self.home_dir),
            "PATH":    "/usr/local/bin:/usr/bin:/bin",
            "LANG":    "en_US.UTF-8",      # hardcoded, non os.environ.get()
            "TERM":    "dumb",
            "PWD":     str(cwd),
            "USER":    "cipher",           # hardcoded
            "LOGNAME": "cipher",           # hardcoded
        }

    def _audit(
        self,
        *,
        user_id: str,
        source: str,
        cmd_raw: str,
        cmd_executed: Optional[str],
        blocked: bool,
        block_reason: Optional[str],
        exit_code: Optional[int],
        duration_ms: Optional[float],
        stdout_preview: Optional[str],
    ) -> None:
        """
        Scrive un record JSON Lines nel log di audit shell.

        SECURITY-STEP1: ogni esecuzione (riuscita o bloccata) viene registrata.
        Predisposto per multi-utente (campo user_id).
        """
        record = {
            "ts":             datetime.now(timezone.utc).isoformat(),
            "user_id":        user_id,
            "source":         source,
            "cmd_raw":        cmd_raw,
            "cmd_executed":   cmd_executed,
            "blocked":        blocked,
            "block_reason":   block_reason,
            "exit_code":      exit_code,
            "duration_ms":    round(duration_ms, 2) if duration_ms is not None else None,
            "stdout_preview": stdout_preview,
        }
        try:
            self._audit_logger.info(json.dumps(record, ensure_ascii=False))
        except Exception:
            pass  # Il log non deve mai bloccare l'esecuzione


# ── Singleton globale ─────────────────────────────────────────────────────────
_guard: Optional[ShellGuard] = None


def get_shell_guard() -> ShellGuard:
    """Ritorna (o crea) il singleton ShellGuard condiviso tra server.py e actions.py."""
    global _guard
    if _guard is None:
        _guard = ShellGuard()
    return _guard
