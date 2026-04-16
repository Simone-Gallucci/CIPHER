"""
modules/shell_guard.py – Esecuzione sicura di comandi shell

Sostituisce subprocess(shell=True) con esecuzione argv-list validata.
Fornisce:
  - Whitelist binari separata per terminale web e shell_exec Telegram
  - Blocco pattern pericolosi nel testo grezzo (;, &, backtick, $(), ecc.)
  - Validazione subcomandi ristretti (git, pip, systemctl, journalctl)
  - Path-traversal check su tutti gli argomenti non-flag
  - Pipe sicure via subprocess.Popen senza shell=True
  - Env pulito senza secret del .env
  - Audit log persistente con rotazione (5 MB × 10 file = max 50 MB)

Usato da:
  server.py   → ShellGuard.validate_and_run_terminal()
  actions.py  → ShellGuard.validate_and_run_shell_exec()
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
_LOGS_DIR  = Config.BASE_DIR / "logs"
_AUDIT_LOG = _LOGS_DIR / "shell_audit.log"

# ── Limiti globali ────────────────────────────────────────────────────────────
MAX_CMD_LENGTH       = 500      # caratteri massimi del comando grezzo
TERMINAL_TIMEOUT     = 10       # secondi, terminale web
SHELL_EXEC_TIMEOUT   = 30       # secondi, shell_exec Telegram
TERMINAL_MAX_OUTPUT  = 51_200   # 50 KB
SHELL_EXEC_MAX_OUT   = 10_240   # 10 KB
JOURNALCTL_MAX_LINES = 500

# ── Whitelist terminale web ───────────────────────────────────────────────────
# Esclusi deliberatamente:
#   bash, sh, python*, nano, less, more → backdoor/interattivi senza TTY
#   env → può eseguire subcomandi arbitrari (env python3 -c "...")
TERMINAL_ALLOWED_BINS: frozenset[str] = frozenset({
    "ls", "ll", "la",
    "cat", "head", "tail",
    "grep", "find", "du", "df", "stat",
    "wc", "sort", "uniq",
    "pwd", "echo", "date", "whoami",
    "touch", "mkdir", "rm", "mv", "cp", "chmod",
    "file", "diff",
    "which", "type", "hash",
    "printenv",
    "tar", "gzip", "gunzip", "zip", "unzip",
})

# ── Whitelist shell_exec Telegram ─────────────────────────────────────────────
# Consenso esplicito già ottenuto dall'utente prima dell'esecuzione.
# Operazioni di scrittura sistema (rm, mv, cp, mkdir...) escluse:
# l'utente le fa dal terminale web con maggiore visibilità.
SHELL_EXEC_ALLOWED_BINS: frozenset[str] = frozenset({
    "ls", "ll", "la",
    "cat", "head", "tail",
    "grep", "find", "du", "df", "stat",
    "wc", "sort", "uniq",
    "pwd", "echo", "date", "whoami",
    "file", "diff",
    "which", "type", "hash",
    "printenv",
    "ps", "top", "free", "uptime",
    "git",         # solo subcomandi read-only, vedi RESTRICTED_SUBCOMMANDS
    "pip", "pip3", # solo list/show/freeze
    "systemctl",   # solo status/is-active/list-units
    "journalctl",  # con --lines forzato ≤ 500
})

# ── Subcomandi ristretti ──────────────────────────────────────────────────────
# Per i binari con frozenset non vuoto, il secondo token dell'argv deve
# essere uno dei subcomandi elencati.
# journalctl non ha subcomandi ma è gestito separatamente con _enforce_journalctl_lines.
RESTRICTED_SUBCOMMANDS: dict[str, frozenset[str]] = {
    "git":       frozenset({"status", "log", "diff", "show", "branch"}),
    "pip":       frozenset({"list", "show", "freeze"}),
    "pip3":      frozenset({"list", "show", "freeze"}),
    "systemctl": frozenset({"status", "is-active", "list-units"}),
}

# ── Flag pericolosi per specifici binari ──────────────────────────────────────
# find -exec/-execdir permette di eseguire comandi arbitrari.
DANGEROUS_FLAGS: dict[str, frozenset[str]] = {
    "find": frozenset({"-exec", "-execdir", "-ok", "-okdir", "-delete"}),
}

# ── Pattern vietati nel testo grezzo del comando ──────────────────────────────
# Controllati prima del parsing, sul testo originale.
# Blocca: ; & ` < > (singoli), || $() ${} newline.
# Permette: | singolo (pipe, gestito separatamente dopo questo check).
_FORBIDDEN_RAW = re.compile(
    r'[;&`<>]'    # separatori shell, redirect, backtick
    r'|\|\|'      # or-chain (||)
    r'|\$[\(\{]'  # command substitution $() o ${}
    r'|[\n\r]'    # newline injection
)


class ShellGuard:
    """
    Validazione e esecuzione sicura di comandi shell per Cipher.

    Due entry point pubblici:
      validate_and_run_terminal()    → terminale web (/api/terminal)
      validate_and_run_shell_exec()  → shell_exec da Telegram (actions.py)

    Ogni esecuzione (riuscita o bloccata) viene registrata nel log di audit
    in formato JSON Lines con rotazione automatica.
    """

    def __init__(
        self,
        home_dir: Path = Config.HOME_DIR,
        audit_log: Path = _AUDIT_LOG,
    ) -> None:
        self.home_dir = home_dir.resolve()
        self._audit_logger = self._setup_audit_logger(audit_log)

    # ── Setup logger ──────────────────────────────────────────────────────────

    @staticmethod
    def _setup_audit_logger(log_path: Path) -> logging.Logger:
        """Configura il logger con RotatingFileHandler (5 MB × 10 file)."""
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger("cipher.shell_audit")
        if not logger.handlers:
            handler = logging.handlers.RotatingFileHandler(
                str(log_path),
                maxBytes=5 * 1024 * 1024,  # 5 MB per file
                backupCount=10,             # max 10 file archiviati → 50 MB totali
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
        Valida ed esegue un comando dal terminale web.

        Supporta pipe (ls | grep x) ma non redirect, chaining o command
        substitution. Ogni comando nella pipeline è validato separatamente
        contro TERMINAL_ALLOWED_BINS e con path-traversal check.

        Args:
            cmd:     Comando grezzo digitato dall'utente nella dashboard.
            cwd:     Working directory corrente (deve essere dentro HOME_DIR).
            user_id: Identificativo utente per il log di audit.

        Returns:
            dict con chiavi:
              output (str), exit_code (int),
              blocked (bool), block_reason (str | None).
        """
        cmd_raw = cmd
        t_start = time.monotonic()

        # ── 1. Lunghezza massima ──────────────────────────────────────────
        if len(cmd) > MAX_CMD_LENGTH:
            reason = f"comando troppo lungo ({len(cmd)} > {MAX_CMD_LENGTH} caratteri)"
            self._audit(user_id=user_id, source="web_terminal", cmd_raw=cmd_raw,
                        cmd_executed=None, blocked=True, block_reason=reason,
                        exit_code=None, duration_ms=None, stdout_preview=None)
            return {"output": "", "exit_code": 1, "blocked": True, "block_reason": reason}

        # ── 2. Pattern vietati nel grezzo ─────────────────────────────────
        m = _FORBIDDEN_RAW.search(cmd)
        if m:
            reason = f"pattern non consentito: '{m.group()}'"
            self._audit(user_id=user_id, source="web_terminal", cmd_raw=cmd_raw,
                        cmd_executed=None, blocked=True, block_reason=reason,
                        exit_code=None, duration_ms=None, stdout_preview=None)
            return {"output": "", "exit_code": 1, "blocked": True, "block_reason": reason}

        # ── 3. Parsing pipeline ───────────────────────────────────────────
        try:
            segments = self._parse_pipeline(cmd)
        except ValueError as e:
            reason = f"errore di parsing: {e}"
            self._audit(user_id=user_id, source="web_terminal", cmd_raw=cmd_raw,
                        cmd_executed=None, blocked=True, block_reason=reason,
                        exit_code=None, duration_ms=None, stdout_preview=None)
            return {"output": "", "exit_code": 1, "blocked": True, "block_reason": reason}

        # ── 4. Validazione di ogni segmento della pipeline ────────────────
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

        # ── 5. Esecuzione ─────────────────────────────────────────────────
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

        Il consenso dell'utente è già stato ottenuto prima di chiamare
        questo metodo (tramite ActionDispatcher._pending_exec).
        Esegue in HOME_DIR con env pulito senza secret.
        Non supporta pipe (Telegram shell è single-command).

        Args:
            command: Comando grezzo proveniente dall'LLM/utente Telegram.
            timeout: Timeout in secondi (cappato a SHELL_EXEC_TIMEOUT).
            user_id: Identificativo utente per il log di audit.

        Returns:
            Stringa di output da restituire all'utente via Telegram.
        """
        cmd_raw = command
        t_start = time.monotonic()
        cwd = self.home_dir
        effective_timeout = min(timeout, SHELL_EXEC_TIMEOUT)

        # ── 1. Lunghezza massima ──────────────────────────────────────────
        if len(command) > MAX_CMD_LENGTH:
            reason = f"comando troppo lungo ({len(command)} > {MAX_CMD_LENGTH} caratteri)"
            self._audit(user_id=user_id, source="telegram_shell_exec", cmd_raw=cmd_raw,
                        cmd_executed=None, blocked=True, block_reason=reason,
                        exit_code=None, duration_ms=None, stdout_preview=None)
            return f"✗ Comando bloccato: {reason}"

        # ── 2. Pattern vietati nel grezzo ─────────────────────────────────
        m = _FORBIDDEN_RAW.search(command)
        if m:
            reason = f"pattern non consentito: '{m.group()}'"
            self._audit(user_id=user_id, source="telegram_shell_exec", cmd_raw=cmd_raw,
                        cmd_executed=None, blocked=True, block_reason=reason,
                        exit_code=None, duration_ms=None, stdout_preview=None)
            return f"✗ Comando bloccato: {reason}"

        # ── 3. Parsing (nessuna pipe per shell_exec) ──────────────────────
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

        # ── 4. Validazione ────────────────────────────────────────────────
        ok, reason, final_argv = self._validate_single_command(
            argv, cwd, SHELL_EXEC_ALLOWED_BINS
        )
        if not ok:
            self._audit(user_id=user_id, source="telegram_shell_exec", cmd_raw=cmd_raw,
                        cmd_executed=None, blocked=True, block_reason=reason,
                        exit_code=None, duration_ms=None, stdout_preview=None)
            return f"✗ Comando bloccato: {reason}"

        # ── 5. Esecuzione ─────────────────────────────────────────────────
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
            return (combined[:SHELL_EXEC_MAX_OUT]
                    if combined else "✓ Comando eseguito senza output.")
        else:
            msg = f"✗ Errore (exit code {result.returncode})"
            if stderr_out:
                msg += f":\n{stderr_out}"
            if stdout_out:
                msg += f"\nOutput:\n{stdout_out}"
            return msg[:SHELL_EXEC_MAX_OUT]

    # ── Metodi interni ────────────────────────────────────────────────────────

    def _parse_pipeline(self, cmd: str) -> list[list[str]]:
        """
        Tokenizza il comando con shlex e lo suddivide in segmenti di pipeline.

        Il simbolo '|' (standalone token) è l'unico separatore consentito.
        I '|' all'interno di stringhe quotate ("a|b") rimangono nei token.

        Returns:
            Lista di argv list (uno per ogni segmento della pipeline).

        Raises:
            ValueError: se il parsing fallisce o la struttura è invalida.
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
            if segments:
                raise ValueError("pipe senza comando successivo")
            raise ValueError("nessun comando trovato dopo il parsing")

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

        Controlli eseguiti in ordine:
          1. Whitelist binari
          2. Restrizione subcomandi (git, pip, systemctl)
          3. Limite righe journalctl
          4. Flag pericolosi (find -exec)
          5. Path traversal su argomenti non-flag

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
        ok, reason = self._validate_paths(final_argv[1:], cwd)
        if not ok:
            return False, reason, []

        return True, "", final_argv

    def _validate_paths(self, args: list[str], cwd: Path) -> tuple[bool, str]:
        """
        Verifica che gli argomenti non-flag si risolvano dentro HOME_DIR.

        Gli argomenti che iniziano con '-' sono trattati come flag e ignorati.
        Gli argomenti assoluti (es. /etc/passwd) vengono risolti direttamente
        e bloccati se non sono sotto HOME_DIR.
        """
        home_str = str(self.home_dir)
        for arg in args:
            if arg.startswith("-"):
                continue  # flag, non è un path
            try:
                resolved = (cwd / arg).resolve()
            except Exception:
                continue  # Path non parsabile: lasciamo che il programma fallisca
            if not str(resolved).startswith(home_str + os.sep) and str(resolved) != home_str:
                return False, f"accesso negato: '{arg}' è fuori da home/ ({resolved})"
        return True, ""

    def _enforce_journalctl_lines(self, argv: list[str]) -> list[str]:
        """
        Rimuove eventuali flag -n/--lines e ne aggiunge uno
        con il valore cappato a JOURNALCTL_MAX_LINES (default 100).
        """
        new_argv = [argv[0]]
        requested = 100  # default se l'utente non specifica
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

        Usa subprocess.Popen con stdin/stdout collegati tramite PIPE.
        Lo stderr degli stadi intermedi viene scartato (DEVNULL) per
        prevenire deadlock da buffer pieno.
        Lo stderr dell'ultimo stadio viene catturato e aggiunto all'output.

        Returns:
            (output_string, exit_code_dell_ultimo_processo)
        """
        procs: list[subprocess.Popen] = []
        clean_env = self._clean_env(cwd)

        try:
            for i, argv in enumerate(segments):
                is_last = (i == len(segments) - 1)
                stdin_pipe = procs[-1].stdout if procs else subprocess.DEVNULL
                proc = subprocess.Popen(
                    argv,
                    stdin=stdin_pipe,
                    stdout=subprocess.PIPE,
                    # Intermedi: DEVNULL su stderr per evitare deadlock.
                    # Ultimo: PIPE per catturare errori da mostrare all'utente.
                    stderr=subprocess.PIPE if is_last else subprocess.DEVNULL,
                    cwd=str(cwd),
                    env=clean_env,
                )
                # Chiude il riferimento locale a stdout del processo precedente:
                # questo permette al processo precedente di ricevere SIGPIPE
                # quando il successivo termina.
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

            # Attendi la terminazione degli stadi intermedi
            for p in procs[:-1]:
                p.wait()

            output = stdout_data.decode("utf-8", errors="replace")
            if stderr_data and stderr_data.strip():
                stderr_str = stderr_data.decode("utf-8", errors="replace").strip()
                output += ("\n" if output.strip() else "") + stderr_str

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

        Non propaga os.environ del processo padre: nessuna API key,
        token o secret del .env viene esposta ai comandi eseguiti.
        """
        return {
            "HOME":    str(self.home_dir),
            "PATH":    "/usr/local/bin:/usr/bin:/bin",
            "LANG":    os.environ.get("LANG", "en_US.UTF-8"),
            "TERM":    "dumb",
            "PWD":     str(cwd),
            "USER":    os.environ.get("USER", "cipher"),
            "LOGNAME": os.environ.get("LOGNAME", "cipher"),
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

        Campi:
          ts              ISO 8601 UTC
          user_id         Identificativo utente (predisposto per multi-utente)
          source          'web_terminal' | 'telegram_shell_exec'
          cmd_raw         Comando grezzo (pre-validazione)
          cmd_executed    Comando effettivamente eseguito (post-validazione, None se bloccato)
          blocked         True se il comando è stato bloccato
          block_reason    Motivo del blocco (None se non bloccato)
          exit_code       Codice di uscita (None se non eseguito)
          duration_ms     Durata in millisecondi (None se non eseguito)
          stdout_preview  Primi 500 caratteri di output (None se non eseguito)
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
# Istanza condivisa tra server.py e actions.py.
_guard: Optional[ShellGuard] = None


def get_shell_guard() -> ShellGuard:
    """Ritorna (o crea) il singleton ShellGuard."""
    global _guard
    if _guard is None:
        _guard = ShellGuard()
    return _guard
