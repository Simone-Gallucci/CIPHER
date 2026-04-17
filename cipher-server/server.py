"""
server.py – Cipher API Server
"""

import os
import subprocess
import tempfile
import time as _time
from collections import defaultdict
from pathlib import Path

from flask import Flask, request, jsonify
from rich.console import Console

from config import Config
from modules.brain import Brain
from modules.listener import Listener
from modules.shell_guard import get_shell_guard  # SECURITY-STEP1
from modules.voice import Voice

# ── Endpoint pubblici (non richiedono auth) ───────────────────────────────
_PUBLIC_PATHS = {"/health", "/web", "/web/"}
_PUBLIC_PREFIXES = ("/web/static/",)

# ── Rate limiting ─────────────────────────────────────────────────────────
_RATE_LIMIT_WINDOW = 60   # secondi
_RATE_LIMIT_MAX    = 30   # max richieste per finestra (endpoint normali)
_RATE_LIMIT_MAX_DASHBOARD = 120  # endpoint dashboard/terminale/file — uso intensivo
_rate_log: dict[str, list[float]] = defaultdict(list)
_rate_log_dash: dict[str, list[float]] = defaultdict(list)

# Endpoint ad alto volume — bucket separato più permissivo
_DASHBOARD_PATHS = {
    "/api/terminal", "/api/terminal/complete",
    "/api/files", "/api/files/read", "/api/files/write",
    "/api/files/upload", "/api/files/download", "/api/files/mkdir",
    "/api/notes", "/api/dashboard", "/api/history",
}

console = Console()
app     = Flask(__name__)

brain         = None
listener      = None
voice         = None
notifier      = None
scheduler     = None
consciousness = None

_SERVER_START = _time.time()


@app.before_request
def check_auth():
    """Controlla il token API su tutte le richieste eccetto /health.
    Se CIPHER_API_TOKEN non è impostato nel .env, l'auth è disabilitata.
    """
    if request.path in _PUBLIC_PATHS or request.path.startswith(_PUBLIC_PREFIXES):
        return None  # path pubblici, nessun controllo
    if not Config.CIPHER_API_TOKEN:
        return None  # token non configurato → auth disabilitata
    token = request.headers.get("X-Cipher-Token", "")
    if token != Config.CIPHER_API_TOKEN:
        return jsonify({"error": "Unauthorized"}), 401
    # Rate limiting per IP — bucket separato per endpoint dashboard
    ip = request.remote_addr or "unknown"
    now = _time.time()
    if request.path in _DASHBOARD_PATHS:
        _rate_log_dash[ip] = [t for t in _rate_log_dash[ip] if now - t < _RATE_LIMIT_WINDOW]
        if len(_rate_log_dash[ip]) >= _RATE_LIMIT_MAX_DASHBOARD:
            return jsonify({"error": "Rate limit exceeded"}), 429
        _rate_log_dash[ip].append(now)
    else:
        _rate_log[ip] = [t for t in _rate_log[ip] if now - t < _RATE_LIMIT_WINDOW]
        if len(_rate_log[ip]) >= _RATE_LIMIT_MAX:
            return jsonify({"error": "Rate limit exceeded"}), 429
        _rate_log[ip].append(now)


def init_brain():
    global brain, listener, voice, notifier, scheduler, consciousness

    console.print("\n[bold]Inizializzazione Cipher Server...[/bold]")

    brain    = Brain()

    # ── SECURITY-STEP4: avviso file orfani nella root memory/ ─────────
    _orphans = [
        f.name for f in Config.MEMORY_DIR.iterdir()
        if f.is_file() and f.suffix in (".json", ".md")
    ] if Config.MEMORY_DIR.exists() else []
    if _orphans:
        console.print(
            f"[yellow]⚠️  File orfani in {Config.MEMORY_DIR} (dovrebbero stare in user_*/): "
            f"{', '.join(sorted(_orphans[:10]))}[/yellow]"
        )

    listener = Listener()
    voice    = Voice()

    from modules.notifier import Notifier
    notifier = Notifier()
    notifier.start()

    from modules.scheduler import Scheduler
    scheduler = Scheduler()
    scheduler._brain = brain   # collegamento per pensiero mattutino LLM
    scheduler.start()

    brain._dispatcher.set_notifier(notifier)
    brain._dispatcher.set_scheduler(scheduler)
    brain._dispatcher.set_llm(brain._call_llm)
    brain._dispatcher.set_llm_silent(brain._call_llm_silent)

    # ── Avvia la coscienza autonoma ───────────────────────────────────
    if Config.CONSCIOUSNESS_ENABLED:
        from modules.consciousness_loop import ConsciousnessLoop
        consciousness = ConsciousnessLoop(brain=brain, voice=voice)
        brain._consciousness = consciousness
        consciousness.start()
    else:
        console.print("[yellow]⚠️  Coscienza autonoma disabilitata (CONSCIOUSNESS_ENABLED=false)[/yellow]")

    # ── Collega i callback Telegram → Brain ───────────────────────────

    def _telegram_message_handler(text: str) -> str:
        """
        Gestisce i messaggi testuali in arrivo da Telegram.
        Notifica la coscienza dell'interazione prima di passare al Brain.
        """
        if consciousness:
            consciousness.notify_interaction()
        return brain.think(text)

    def _telegram_file_handler(path: str, instruction: str) -> str:
        """
        Gestisce i file in arrivo da Telegram.
        Passa il file al dispatcher con l'istruzione di Simone.
        """
        if consciousness:
            consciousness.notify_interaction()
        return brain._dispatcher.execute(
            "file_read", {"path": path, "instruction": instruction}
        )

    notifier.set_message_callback(_telegram_message_handler)
    notifier.set_file_callback(_telegram_file_handler)

    console.print("[green]✓ Notifier Telegram avviato[/green]")
    console.print("[green]✓ Scheduler avviato[/green]")
    console.print("[bold green]✓ Coscienza autonoma avviata[/bold green]")
    console.print("[green]✓ Server pronto[/green]")


def convert_to_pcm(audio_bytes: bytes, content_type: str = "") -> bytes:
    """
    Converte audio a PCM 16bit 16000Hz mono.
    Se il Content-Type indica già PCM/raw, lo restituisce direttamente.
    Altrimenti usa ffmpeg per convertire da 3gpp, wav, ecc.
    """
    if "pcm" in content_type or "raw" in content_type or "octet" in content_type:
        return audio_bytes

    with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as tmp_in:
        tmp_in.write(audio_bytes)
        tmp_in_path = tmp_in.name

    tmp_out_path = tmp_in_path + ".pcm"

    try:
        subprocess.run([
            "ffmpeg", "-y",
            "-i", tmp_in_path,
            "-ar", "16000",
            "-ac", "1",
            "-f", "s16le",
            tmp_out_path
        ], capture_output=True, check=True)
        with open(tmp_out_path, "rb") as f:
            return f.read()
    finally:
        if os.path.exists(tmp_in_path):
            os.unlink(tmp_in_path)
        if os.path.exists(tmp_out_path):
            os.unlink(tmp_out_path)


@app.route("/health", methods=["GET"])
def health():
    from modules import llm_usage
    from modules.admin_manager import admin_exists
    profile_file = get_user_memory_dir(get_system_owner_id()) / "profile.json"
    confidence = 0.0
    if profile_file.exists():
        try:
            import json
            profile = json.loads(profile_file.read_text(encoding="utf-8"))
            confidence = profile.get("confidence_score", 0.0)
        except Exception:
            pass
    return jsonify({
        "status": "ok",
        "model": Config.OPENROUTER_MODEL,
        "background_model": Config.BACKGROUND_MODEL,
        "consciousness": consciousness is not None and consciousness._running,
        "admin_bound": admin_exists(),
        "confidence": confidence,
        "llm_calls_today": sum(llm_usage.get_today().values()),
    })


@app.route("/chat", methods=["POST"])
def chat():
    data       = request.get_json(silent=True) or {}
    message    = data.get("message", "").strip()
    image_b64  = data.get("image_b64")
    media_type = data.get("media_type", "image/jpeg")
    source     = data.get("source", "")
    sender_id  = str(data.get("chat_id", ""))
    if not message and not image_b64:
        return jsonify({"error": "messaggio vuoto"}), 400
    try:
        _t0 = _time.time()
        response = brain.think(message, image_b64=image_b64, media_type=media_type, source=source, sender_id=sender_id)
        console.print(f"[dim]⏱ chat: {_time.time() - _t0:.1f}s[/dim]")
        return jsonify({"response": response})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/memory", methods=["GET"])
def get_memory():
    return jsonify(brain._memory.profile)


@app.route("/memory/interests", methods=["GET"])
def get_interests():
    interests_file = get_user_memory_dir(get_system_owner_id()) / "cipher_interests.json"
    if not interests_file.exists():
        return jsonify({"interests": []})
    try:
        import json
        interests = json.loads(interests_file.read_text(encoding="utf-8"))
        return jsonify({"interests": interests})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/reset", methods=["POST"])
def reset():
    brain.reset()
    return jsonify({"status": "resettato"})


@app.route("/stt", methods=["POST"])
def stt():
    audio_data = request.data
    if not audio_data:
        return jsonify({"text": ""}), 400
    try:
        audio_data = convert_to_pcm(audio_data, request.content_type or "")
        text = listener.transcribe_audio(audio_data)
        return jsonify({"text": text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/wake", methods=["POST"])
def wake():
    audio_data = request.data
    if not audio_data:
        return jsonify({"detected": False}), 400
    try:
        audio_data = convert_to_pcm(audio_data, request.content_type or "")
        text = listener.transcribe_audio(audio_data).lower().strip()
        detected = any(w in text for w in Config.WAKE_WORDS)
        console.print(f"[cyan]Wake STT: '{text}' → {detected}[/cyan]")
        return jsonify({"detected": detected, "text": text})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/tts", methods=["POST"])
def tts():
    data = request.get_json(silent=True) or {}
    text = data.get("text", "").strip()
    if not text:
        return jsonify({"error": "testo vuoto"}), 400
    try:
        audio_bytes = voice.synthesize(text)
        if not audio_bytes:
            return jsonify({"error": "TTS non disponibile"}), 503
        return app.response_class(
            response=audio_bytes,
            status=200,
            mimetype="audio/mpeg"
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/consciousness/status", methods=["GET"])
def consciousness_status():
    if not consciousness:
        return jsonify({"error": "coscienza non inizializzata"}), 503
    return jsonify({
        "status": consciousness.status(),
        "emotional_state": consciousness.emotional_state,
    })


@app.route("/consciousness/thoughts", methods=["GET"])
def consciousness_thoughts():
    thoughts_file = get_user_memory_dir(get_system_owner_id()) / "thoughts.md"
    if not thoughts_file.exists():
        return jsonify({"thoughts": "Nessun pensiero ancora."})
    try:
        content = thoughts_file.read_text(encoding="utf-8")
        return jsonify({"thoughts": content[-3000:]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/consciousness/goals", methods=["GET"])
def consciousness_goals():
    goals_file = get_user_memory_dir(get_system_owner_id()) / "goals.md"
    if not goals_file.exists():
        return jsonify({"goals": "Nessun obiettivo attivo."})
    try:
        content = goals_file.read_text(encoding="utf-8")
        return jsonify({"goals": content})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/web/static/<path:filename>")
def serve_web_static(filename):
    from pathlib import Path
    from flask import send_from_directory
    static_dir = Path(__file__).parent / "web" / "static"
    safe = (static_dir / filename).resolve()
    if not str(safe).startswith(str(static_dir.resolve())):
        return "", 403
    return send_from_directory(str(static_dir), filename)


@app.route("/web")
@app.route("/web/")
def serve_web():
    from pathlib import Path
    html_path = Path(__file__).parent / "web" / "index.html"
    if not html_path.exists():
        return "Dashboard not found", 404
    html = html_path.read_text(encoding="utf-8")
    html = html.replace("{{CIPHER_TOKEN}}", Config.CIPHER_API_TOKEN or "")
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/api/dashboard", methods=["GET"])
def dashboard_data():
    import json
    from datetime import datetime, timedelta
    mem = get_user_memory_dir(get_system_owner_id())

    # profile.json
    profile = {}
    try:
        profile = json.loads((mem / "profile.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    confidence = profile.get("confidence_score", 0.0)

    if confidence < 0.2:
        band = "Conoscente"
    elif confidence < 0.4:
        band = "Amico"
    elif confidence < 0.6:
        band = "Amico stretto"
    elif confidence < 0.8:
        band = "Confidente"
    else:
        band = "Migliore amico"

    # goals
    goals_data = []
    try:
        all_goals = json.loads((mem / "goals.json").read_text(encoding="utf-8")).get("goals", [])
        goals_data = [g for g in all_goals if g.get("status") != "completed"][-5:]
    except Exception:
        pass

    # cipher state
    cipher_state = {}
    try:
        raw = json.loads((mem / "cipher_state.json").read_text(encoding="utf-8"))
        cipher_state = {k: v for k, v in raw.items() if k not in ("emotional_reason",)}
    except Exception:
        pass

    # emotional log (Simone)
    simone_recent = []
    try:
        elog = json.loads((mem / "emotional_log.json").read_text(encoding="utf-8"))
        simone_recent = elog[-5:] if isinstance(elog, list) else []
    except Exception:
        pass

    # action log — ultimi 7 giorni, max 20 entry
    action_log = []
    try:
        alog = json.loads((mem / "action_log.json").read_text(encoding="utf-8"))
        if isinstance(alog, list):
            cutoff = (datetime.now() - timedelta(days=7)).isoformat()[:10]
            recent = [e for e in alog if isinstance(e, dict) and str(e.get("timestamp", ""))[:10] >= cutoff]
            action_log = recent[-20:] if recent else alog[-20:]
    except Exception:
        pass

    # episodes count
    episodes_count = 0
    try:
        ep = json.loads((mem / "episodes.json").read_text(encoding="utf-8"))
        episodes_count = len(ep) if isinstance(ep, list) else 0
    except Exception:
        pass

    # short term count
    short_term_count = 0
    try:
        st = json.loads((mem / "short_term.json").read_text(encoding="utf-8"))
        short_term_count = len(st) if isinstance(st, list) else 0
    except Exception:
        pass

    # last message timestamp
    last_message = None
    try:
        hist = json.loads((mem / "active_history.json").read_text(encoding="utf-8"))
        if hist:
            last_message = hist[-1].get("timestamp") or hist[-1].get("content", "")[:30]
    except Exception:
        pass

    # llm calls
    from modules import llm_usage
    calls_today = sum(llm_usage.get_today().values())

    # calendar — raw events for today + tomorrow
    calendar_events = []
    try:
        from datetime import datetime, timedelta, timezone
        cal_svc = None
        if brain and brain._dispatcher and brain._dispatcher._calendar:
            cal_svc = brain._dispatcher._calendar._service
        else:
            from modules.google_cal import GoogleCalendar
            _gc = GoogleCalendar()
            cal_svc = _gc._service
        if cal_svc:
            now = datetime.now(timezone.utc)
            end = now + timedelta(days=2)
            result = cal_svc.events().list(
                calendarId="primary",
                timeMin=now.isoformat(),
                timeMax=end.isoformat(),
                maxResults=10,
                singleEvents=True,
                orderBy="startTime",
            ).execute()
            for e in result.get("items", []):
                start = e["start"].get("dateTime", e["start"].get("date", ""))
                try:
                    dt = datetime.fromisoformat(start)
                    time_str = dt.strftime("%d/%m %H:%M")
                except Exception:
                    time_str = start
                calendar_events.append({
                    "title": e.get("summary", "(senza titolo)"),
                    "time": time_str,
                    "id": e.get("id", ""),
                })
    except Exception:
        pass

    uptime = int(_time.time() - _SERVER_START)

    return jsonify({
        "status": {
            "online": True,
            "uptime": uptime,
            "llm_calls_today": calls_today,
            "model": Config.OPENROUTER_MODEL,
            "provider": Config._provider,
            "last_message": last_message,
            "consciousness": consciousness is not None and consciousness._running,
        },
        "memory": {
            "confidence": confidence,
            "confidence_band": band,
            "episodes_count": episodes_count,
            "short_term_count": short_term_count,
            "user_name": profile.get("nome") or profile.get("name") or "",
        },
        "goals": goals_data,
        "emotional": {
            "cipher_state": cipher_state,
            "simone_recent": simone_recent,
        },
        "calendar": calendar_events,
        "action_log": action_log,
    })


@app.route("/api/history", methods=["GET"])
def chat_history():
    return jsonify({"messages": brain._history if brain else []})


# ── File manager endpoints ─────────────────────────────────────────────────
# SECURITY-STEP2: _files_root() e _safe_file_path() rimossi.
# Sostituiti da PathGuard.validate_path() che usa relative_to() invece di
# str.startswith() (bypassabile con directory col nome adiacente es. home_evil/).
# secure_filename aggiunto in files_upload per sanitizzare f.filename.

from modules.path_guard import get_path_guard, PathTraversalError
from modules.auth import get_current_user_id, get_user_memory_dir, get_system_owner_id


def _pg_validate(path: str, operation: str):
    """
    Helper interno: valida path per l'utente corrente via PathGuard.
    Ritorna (target, None) se ok, (None, response) se errore.
    """
    try:
        target = get_path_guard().validate_path(
            get_current_user_id(), path or ".", operation
        )
        return target, None
    except PathTraversalError as e:
        return None, (jsonify({"error": str(e)}), 403)


@app.route("/api/files", methods=["GET"])
def files_list():
    # SECURITY-STEP2: PathGuard.validate_path() invece di _safe_file_path()
    path = request.args.get("path", "")
    target, err = _pg_validate(path, "LIST")
    if err:
        return err
    if not target.exists():
        return jsonify({"error": "Percorso non trovato"}), 404
    if not target.is_dir():
        return jsonify({"error": "Non è una directory"}), 400
    items = []
    for item in sorted(target.iterdir(), key=lambda x: (x.is_file(), x.name)):
        if item.name.startswith("."):
            continue
        items.append({
            "name": item.name,
            "type": "file" if item.is_file() else "dir",
            "size": item.stat().st_size if item.is_file() else None,
        })
    return jsonify({"path": path, "items": items})


@app.route("/api/files", methods=["DELETE"])
def files_delete():
    import shutil
    data = request.get_json(silent=True) or {}
    path = data.get("path", "")
    if not path:
        return jsonify({"error": "path mancante"}), 400
    # SECURITY-STEP2: PathGuard invece di _safe_file_path()
    target, err = _pg_validate(path, "DELETE")
    if err:
        return err
    if not target.exists():
        return jsonify({"error": "File non trovato"}), 404
    try:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/files/read", methods=["GET"])
def files_read():
    path = request.args.get("path", "")
    if not path:
        return jsonify({"error": "path mancante"}), 400
    # SECURITY-STEP2: PathGuard invece di _safe_file_path()
    target, err = _pg_validate(path, "READ")
    if err:
        return err
    if not target.exists() or not target.is_file():
        return jsonify({"error": "File non trovato"}), 404
    if target.stat().st_size > 1_048_576:  # 1 MB
        return jsonify({"error": "File troppo grande (max 1MB)"}), 413
    try:
        content = target.read_text(encoding="utf-8")
        return jsonify({"content": content})
    except UnicodeDecodeError:
        return jsonify({"error": "File non leggibile come testo"}), 415
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/files/write", methods=["POST"])
def files_write():
    data = request.get_json(silent=True) or {}
    path = data.get("path", "")
    content = data.get("content", "")
    if not path:
        return jsonify({"error": "path mancante"}), 400
    # SECURITY-STEP2: PathGuard invece di _safe_file_path()
    target, err = _pg_validate(path, "WRITE")
    if err:
        return err
    try:
        # Crea .bak se il file esiste già
        if target.exists():
            import shutil
            bak = target.with_suffix(target.suffix + ".bak")
            shutil.copy2(target, bak)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/files/upload", methods=["POST"])
def files_upload():
    from werkzeug.utils import secure_filename
    path = request.args.get("path", "")
    if "file" not in request.files:
        return jsonify({"error": "nessun file"}), 400
    f = request.files["file"]

    # SECURITY-STEP2: secure_filename sanitizza il nome (rimuove '/', '..', caratteri
    # speciali). Impedisce path traversal via f.filename = "../../etc/cron.d/evil".
    safe_name = secure_filename(f.filename or "")
    if not safe_name:
        return jsonify({"error": "nome file non valido"}), 400

    # Valida la subdir destinazione (se specificata)
    if path:
        subdir, err = _pg_validate(path, "WRITE")
        if err:
            return err
        subdir.mkdir(parents=True, exist_ok=True)
        target_rel = path.rstrip("/") + "/" + safe_name
    else:
        target_rel = safe_name

    # Valida il path completo del file destinazione
    target, err = _pg_validate(target_rel, "WRITE")
    if err:
        return err

    file_data = f.read(10_485_761)  # legge un byte in più per verificare limite
    if len(file_data) > 10_485_760:
        return jsonify({"error": "File troppo grande (max 10MB)"}), 413
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(file_data)
        return jsonify({"ok": True, "name": safe_name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/files/download", methods=["GET"])
def files_download():
    from flask import send_file
    path = request.args.get("path", "")
    if not path:
        return jsonify({"error": "path mancante"}), 400
    # SECURITY-STEP2: PathGuard invece di _safe_file_path()
    target, err = _pg_validate(path, "READ")
    if err:
        return err
    if not target.exists() or not target.is_file():
        return jsonify({"error": "File non trovato"}), 404
    return send_file(str(target), as_attachment=True, download_name=target.name)


@app.route("/api/files/mkdir", methods=["POST"])
def files_mkdir():
    data = request.get_json(silent=True) or {}
    path = data.get("path", "")
    if not path:
        return jsonify({"error": "path mancante"}), 400
    # SECURITY-STEP2: PathGuard invece di _safe_file_path()
    target, err = _pg_validate(path, "WRITE")
    if err:
        return err
    try:
        target.mkdir(parents=True, exist_ok=True)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Terminal endpoint ──────────────────────────────────────────────────────
# SECURITY-STEP1: _TERMINAL_BLOCKED rimossa (blocklist bypassabile con
# separatori, backtick, $()). Sostituita da whitelist in shell_guard.py.

_TERMINAL_HELP = """\
Cipher restricted shell — cwd: home/

COMANDI FILE E CARTELLE
  ls [path]              elenca file e cartelle
  ls -la [path]          elenca con dettagli (permessi, dimensione, data)
  pwd                    mostra cartella corrente
  cd cartella            entra in una sottocartella (max: home/)
  cd ..                  torna alla cartella superiore
  cd                     torna alla root home/
  cat file               mostra contenuto di un file
  head -n N file         prime N righe
  tail -n N file         ultime N righe
  touch file             crea file vuoto (o aggiorna data)
  mkdir nome             crea cartella
  rm -r nome             elimina file o cartella (anche non vuota)
  mv src dst             sposta o rinomina
  cp src dst             copia
  find . -name "*.txt"   cerca file per nome (senza -exec)
  du -sh *               dimensione di file e cartelle
  stat file              info dettagliate su un file

TESTO E RICERCA
  grep "testo" file      cerca testo in un file
  grep -r "testo" .      cerca testo in tutti i file
  wc -l file             conta righe
  sort file              ordina righe
  uniq file              rimuove duplicati

PIPE
  cmd1 | cmd2            passa output di cmd1 a cmd2

UTILITY
  echo "testo"           stampa testo
  date                   data e ora corrente
  whoami                 utente corrente
  df -h                  spazio disco

SICUREZZA (whitelist — non blocklist)
  Binari consentiti: ls, cat, head, tail, grep, find, du, df, stat, wc,
    sort, uniq, pwd, echo, date, whoami, file, diff, which, type, hash,
    touch, mkdir, rm, mv, cp, chmod
  Redirect (> >> <), chaining (; && || &), backtick, $() bloccati
  Tutti i path validati contro home/ — nessun accesso esterno
  Env isolato: solo HOME, PATH, LANG, TERM (nessun secret)

LIMITI
  timeout: 10 secondi — output max: 50 KB — comando max: 500 caratteri
  working dir: home/ — cd non può uscire da home/
"""


@app.route("/api/terminal", methods=["POST"])
def terminal_run():
    # SECURITY-STEP1: endpoint riscritto per usare ShellGuard.
    # Rimossi: subprocess.run(shell=True), _TERMINAL_BLOCKED, {**os.environ}.
    try:
        data = request.get_json(silent=True) or {}
        cmd = data.get("cmd", "").strip()
        if not cmd:
            return jsonify({"error": "comando vuoto"}), 400
        if cmd == "help":
            return jsonify({"output": _TERMINAL_HELP, "exit_code": 0})

        # SECURITY-STEP2: get_user_home() invece di _files_root() (rimossa)
        from modules.path_guard import get_user_home
        root = get_user_home(get_current_user_id())

        # Risolvi cwd inviato dal client (relativo a root)
        client_cwd = data.get("cwd", "").strip().lstrip("/")
        if client_cwd:
            candidate = (root / client_cwd).resolve()
            cwd = candidate if str(candidate).startswith(str(root)) and candidate.is_dir() else root
        else:
            cwd = root

        def _rel(p: Path) -> str:
            try:
                r = str(p.relative_to(root))
                return "" if r == "." else r
            except Exception:
                return ""

        # Gestisci cd lato server (non richiede subprocess)
        if cmd == "cd" or cmd.startswith("cd "):
            parts = cmd.split(None, 1)
            target_rel = parts[1].strip() if len(parts) > 1 else ""
            if not target_rel or target_rel == "~":
                return jsonify({"output": "", "exit_code": 0, "cwd": ""})
            new_cwd = (cwd / target_rel).resolve()
            if not str(new_cwd).startswith(str(root)):
                return jsonify({"output": "cd: accesso negato (fuori da home/)", "exit_code": 1, "cwd": _rel(cwd)})
            if not new_cwd.is_dir():
                return jsonify({"output": f"cd: {target_rel}: cartella non trovata", "exit_code": 1, "cwd": _rel(cwd)})
            return jsonify({"output": "", "exit_code": 0, "cwd": _rel(new_cwd)})

        # SECURITY-STEP1: delega tutto a ShellGuard (whitelist + path check +
        # env pulito + audit log). Nessun subprocess.run(shell=True) qui.
        result = get_shell_guard().validate_and_run_terminal(cmd=cmd, cwd=cwd)

        if result["blocked"]:
            return jsonify({"error": f"Comando bloccato: {result['block_reason']}"}), 403

        return jsonify({
            "output":    result["output"],
            "exit_code": result["exit_code"],
            "cwd":       _rel(cwd),
        })

    except Exception as e:
        return jsonify({"error": f"Errore interno: {e}", "output": "", "exit_code": 1}), 500


# ── Terminal autocomplete ───────────────────────────────────────────────────
@app.route("/api/terminal/complete", methods=["POST"])
def terminal_complete():
    try:
        data = request.get_json(silent=True) or {}
        partial = data.get("partial", "")
        client_cwd = data.get("cwd", "").strip().lstrip("/")
        # SECURITY-STEP2: get_user_home() invece di _files_root() (rimossa)
        from modules.path_guard import get_user_home
        root = get_user_home(get_current_user_id())
        if client_cwd:
            candidate = (root / client_cwd).resolve()
            cwd = candidate if str(candidate).startswith(str(root)) and candidate.is_dir() else root
        else:
            cwd = root
        p = Path(partial)
        search_dir = (cwd / p.parent).resolve() if str(p.parent) != "." else cwd
        prefix = p.name
        if not str(search_dir).startswith(str(root)):
            return jsonify({"matches": []})
        matches = []
        for item in sorted(search_dir.iterdir()):
            if item.name.startswith(prefix):
                rel = str(item.relative_to(cwd))
                matches.append(rel + ("/" if item.is_dir() else ""))
        return jsonify({"matches": matches})
    except Exception as e:
        return jsonify({"matches": [], "error": str(e)})


# ── Notes endpoint ─────────────────────────────────────────────────────────

def _notes_file():
    # SECURITY-STEP2: get_user_home() invece di Config.HOME_DIR
    from modules.path_guard import get_user_home
    from modules.auth import get_current_user_id
    return get_user_home(get_current_user_id()) / "notes.md"


@app.route("/api/notes", methods=["GET"])
def notes_get():
    nf = _notes_file()
    if not nf.exists():
        return jsonify({"content": ""})
    try:
        return jsonify({"content": nf.read_text(encoding="utf-8")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/notes", methods=["POST"])
def notes_save():
    data = request.get_json(silent=True) or {}
    content = data.get("content", "")
    try:
        _notes_file().write_text(content, encoding="utf-8")
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Calendar CRUD endpoints ───────────────────────────────────────────────

def _get_cal_service():
    """Return a Google Calendar service object, reusing Brain's if available."""
    if brain and brain._dispatcher and brain._dispatcher._calendar:
        return brain._dispatcher._calendar._service
    from modules.google_cal import GoogleCalendar
    return GoogleCalendar()._service


@app.route("/api/calendar", methods=["GET"])
def calendar_list():
    """List events. ?days=7 (default 7), ?q=search"""
    try:
        from datetime import datetime, timedelta, timezone
        svc = _get_cal_service()
        days = int(request.args.get("days", 7))
        q = request.args.get("q", "")
        now = datetime.now(timezone.utc)
        end = now + timedelta(days=max(days, 1))
        kwargs = {
            "calendarId": "primary",
            "timeMin": now.isoformat(),
            "timeMax": end.isoformat(),
            "maxResults": 50,
            "singleEvents": True,
            "orderBy": "startTime",
        }
        if q:
            kwargs["q"] = q
        result = svc.events().list(**kwargs).execute()
        events = []
        for e in result.get("items", []):
            start = e["start"].get("dateTime", e["start"].get("date", ""))
            end_t = e["end"].get("dateTime", e["end"].get("date", ""))
            events.append({
                "id": e.get("id", ""),
                "title": e.get("summary", "(senza titolo)"),
                "start": start,
                "end": end_t,
                "location": e.get("location", ""),
                "description": e.get("description", ""),
            })
        return jsonify({"events": events})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/calendar", methods=["POST"])
def calendar_create():
    """Create event. JSON: {title, start, end?, description?, location?}"""
    try:
        data = request.get_json(silent=True) or {}
        title = data.get("title", "").strip()
        start = data.get("start", "").strip()
        if not title or not start:
            return jsonify({"error": "title e start sono obbligatori"}), 400
        from datetime import datetime, timedelta
        start_dt = datetime.fromisoformat(start)
        end_str = data.get("end", "").strip()
        end_dt = datetime.fromisoformat(end_str) if end_str else start_dt + timedelta(hours=1)
        event_body = {
            "summary": title,
            "description": data.get("description", ""),
            "location": data.get("location", ""),
            "start": {"dateTime": start_dt.isoformat(), "timeZone": "Europe/Rome"},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": "Europe/Rome"},
        }
        svc = _get_cal_service()
        created = svc.events().insert(calendarId="primary", body=event_body).execute()
        return jsonify({"ok": True, "id": created.get("id", ""), "link": created.get("htmlLink", "")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/calendar/<event_id>", methods=["PUT"])
def calendar_update(event_id):
    """Update event. JSON: {title?, start?, end?, description?, location?}"""
    try:
        data = request.get_json(silent=True) or {}
        svc = _get_cal_service()
        existing = svc.events().get(calendarId="primary", eventId=event_id).execute()
        if "title" in data and data["title"].strip():
            existing["summary"] = data["title"].strip()
        if "description" in data:
            existing["description"] = data["description"]
        if "location" in data:
            existing["location"] = data["location"]
        from datetime import datetime, timedelta
        if "start" in data and data["start"].strip():
            start_dt = datetime.fromisoformat(data["start"].strip())
            existing["start"] = {"dateTime": start_dt.isoformat(), "timeZone": "Europe/Rome"}
        if "end" in data and data["end"].strip():
            end_dt = datetime.fromisoformat(data["end"].strip())
            existing["end"] = {"dateTime": end_dt.isoformat(), "timeZone": "Europe/Rome"}
        updated = svc.events().update(calendarId="primary", eventId=event_id, body=existing).execute()
        return jsonify({"ok": True, "id": updated.get("id", "")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/calendar/<event_id>", methods=["DELETE"])
def calendar_delete(event_id):
    """Delete event by ID."""
    try:
        svc = _get_cal_service()
        svc.events().delete(calendarId="primary", eventId=event_id).execute()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    init_brain()
    port = int(os.getenv("SERVER_PORT", 5000))
    console.print(f"[cyan]🌐 Server in ascolto su porta {port}[/cyan]")
    app.run(host="127.0.0.1", port=port)
