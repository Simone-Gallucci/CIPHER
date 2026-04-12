"""
server.py – Cipher API Server
"""

import os
import subprocess
import tempfile

from flask import Flask, request, jsonify
from rich.console import Console

from config import Config
from modules.brain import Brain
from modules.listener import Listener
from modules.voice import Voice

# ── Endpoint pubblici (non richiedono auth) ───────────────────────────────
_PUBLIC_PATHS = {"/health"}

console = Console()
app     = Flask(__name__)

brain         = None
listener      = None
voice         = None
notifier      = None
scheduler     = None
consciousness = None


@app.before_request
def check_auth():
    """Controlla il token API su tutte le richieste eccetto /health.
    Se CIPHER_API_TOKEN non è impostato nel .env, l'auth è disabilitata.
    """
    if request.path in _PUBLIC_PATHS:
        return None  # health check pubblico, nessun controllo
    if not Config.CIPHER_API_TOKEN:
        return None  # token non configurato → auth disabilitata
    token = request.headers.get("X-Cipher-Token", "")
    if token != Config.CIPHER_API_TOKEN:
        return jsonify({"error": "Unauthorized"}), 401


def init_brain():
    global brain, listener, voice, notifier, scheduler, consciousness

    console.print("\n[bold]Inizializzazione Cipher Server...[/bold]")

    brain    = Brain()
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
    return jsonify({"status": "ok", "model": Config.OPENROUTER_MODEL})


@app.route("/chat", methods=["POST"])
def chat():
    import time as _time
    data       = request.get_json(silent=True) or {}
    message    = data.get("message", "").strip()
    image_b64  = data.get("image_b64")
    media_type = data.get("media_type", "image/jpeg")
    if not message and not image_b64:
        return jsonify({"error": "messaggio vuoto"}), 400
    try:
        _t0 = _time.time()
        response = brain.think(message, image_b64=image_b64, media_type=media_type)
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
    interests_file = Config.MEMORY_DIR / "cipher_interests.json"
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
    thoughts_file = Config.MEMORY_DIR / "thoughts.md"
    if not thoughts_file.exists():
        return jsonify({"thoughts": "Nessun pensiero ancora."})
    try:
        content = thoughts_file.read_text(encoding="utf-8")
        return jsonify({"thoughts": content[-3000:]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/consciousness/goals", methods=["GET"])
def consciousness_goals():
    goals_file = Config.MEMORY_DIR / "goals.md"
    if not goals_file.exists():
        return jsonify({"goals": "Nessun obiettivo attivo."})
    try:
        content = goals_file.read_text(encoding="utf-8")
        return jsonify({"goals": content})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    init_brain()
    port = int(os.getenv("SERVER_PORT", 5000))
    console.print(f"[cyan]🌐 Server in ascolto su porta {port}[/cyan]")
    app.run(host="127.0.0.1", port=port)
