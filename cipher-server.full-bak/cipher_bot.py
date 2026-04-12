"""
cipher_bot.py – Cipher Telegram Client
Interfaccia Telegram per Cipher con supporto media:
  - Testo           →  /chat sul server Cipher
  - Voice / Audio   →  Vosk STT  →  /chat sul server Cipher
  - Foto            →  Claude Vision (OpenRouter, diretto)
  - Documenti       →  FileEngine via /chat (con istruzione di Simone)

Solo l'utente autorizzato (TELEGRAM_ALLOWED_ID) può interagire.
"""

import base64
import json
import logging
import os
import subprocess
import tempfile
import wave
from io import BytesIO
from pathlib import Path

import requests
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

from config import Config

# ── Configurazione ────────────────────────────────────────────────────
TELEGRAM_TOKEN     = Config.TELEGRAM_BOT_TOKEN or os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_ID         = Config.TELEGRAM_ALLOWED_ID or int(os.getenv("TELEGRAM_ALLOWED_ID", "0"))
CIPHER_SERVER_URL  = os.getenv("CIPHER_SERVER_URL", "http://100.127.57.5:5000")
OPENROUTER_API_KEY = Config.OPENROUTER_API_KEY
VOSK_MODEL_PATH    = Config.VOSK_MODEL_PATH

UPLOADS_DIR = Path(__file__).parent / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("cipher.telegram")

# ── System prompt Vision ──────────────────────────────────────────────
VISION_SYSTEM_PROMPT = """Sei Cipher, assistente AI personale di Simone.
Descrivi l'immagine in modo diretto e conciso. Rispondi in italiano."""

# ── Caricamento Vosk ──────────────────────────────────────────────────
vosk_model = None
try:
    from vosk import Model, KaldiRecognizer
    if os.path.isdir(VOSK_MODEL_PATH):
        vosk_model = Model(VOSK_MODEL_PATH)
        log.info("Modello Vosk caricato da '%s'", VOSK_MODEL_PATH)
    else:
        log.warning("VOSK_MODEL_PATH '%s' non trovato.", VOSK_MODEL_PATH)
except ImportError:
    log.warning("Libreria vosk non installata.")


# ── Guardia accesso ───────────────────────────────────────────────────

def is_authorized(update: Update) -> bool:
    return update.effective_user.id == ALLOWED_ID


# ── Helper: download file da Telegram ────────────────────────────────

def _download_telegram_file(file_id: str) -> bytes:
    r = requests.get(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile",
        params={"file_id": file_id},
        timeout=30,
    )
    r.raise_for_status()
    file_path = r.json()["result"]["file_path"]
    data = requests.get(
        f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}",
        timeout=120,
    )
    data.raise_for_status()
    return data.content


# ── Helper: invia testo al server Cipher ─────────────────────────────

def _ask_cipher(text: str, image_b64: str | None = None, media_type: str = "image/jpeg") -> str:
    payload: dict = {"message": text}
    if image_b64:
        payload["image_b64"] = image_b64
        payload["media_type"] = media_type
    try:
        resp = requests.post(
            f"{CIPHER_SERVER_URL}/chat",
            json=payload,
            timeout=300,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("response") or data.get("message") or "Nessuna risposta."
    except requests.exceptions.ConnectionError:
        return "⚠️ Server Cipher non raggiungibile."
    except requests.exceptions.Timeout:
        return "⚠️ Server Cipher non risponde (timeout)."
    except Exception as e:
        return "⚠️ Errore di comunicazione con il server."


# ── Helper: Claude Vision ─────────────────────────────────────────────

def _ask_claude_vision(image_bytes: bytes, caption: str) -> str:
    if not OPENROUTER_API_KEY:
        return "⚠️ OPENROUTER_API_KEY non configurata."

    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    payload = {
        "model": Config.OPENROUTER_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                {"type": "text", "text": caption},
            ],
        }],
        "system": VISION_SYSTEM_PROMPT,
        "max_tokens": 1024,
    }
    try:
        resp = requests.post(
            f"{Config.OPENROUTER_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=300,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"⚠️ Errore Claude Vision: {e}"


# ── Helper: trascrizione audio con Vosk ──────────────────────────────

def _transcribe_audio(raw_bytes: bytes) -> str:
    if vosk_model is None:
        return "[Vosk non disponibile]"

    with tempfile.TemporaryDirectory() as tmpdir:
        input_path  = os.path.join(tmpdir, "input.ogg")
        output_path = os.path.join(tmpdir, "output.wav")

        with open(input_path, "wb") as f:
            f.write(raw_bytes)

        result = subprocess.run(
            ["ffmpeg", "-y", "-i", input_path, "-ar", "16000", "-ac", "1", "-f", "wav", output_path],
            capture_output=True, timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg fallito: {result.stderr.decode(errors='replace')}")

        with wave.open(output_path, "rb") as wf:
            rec   = KaldiRecognizer(vosk_model, wf.getframerate())
            parts = []
            while True:
                data = wf.readframes(4000)
                if not data:
                    break
                if rec.AcceptWaveform(data):
                    parts.append(json.loads(rec.Result()).get("text", ""))
            parts.append(json.loads(rec.FinalResult()).get("text", ""))

    transcript = " ".join(t for t in parts if t).strip()
    return transcript or "[Nessun testo riconosciuto]"


# ── Helper: TTS via server Cipher ────────────────────────────────────

def _synthesize_tts(text: str) -> bytes | None:
    """Chiama /tts sul server Cipher e restituisce i bytes MP3."""
    try:
        resp = requests.post(
            f"{CIPHER_SERVER_URL}/tts",
            json={"text": text},
            timeout=60,
        )
        if resp.status_code == 200:
            return resp.content
    except Exception as e:
        log.warning("TTS fallito: %s", e)
    return None


def _mp3_to_ogg(mp3_bytes: bytes) -> bytes | None:
    """Converte MP3 bytes in OGG/Opus per Telegram voice note."""
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            mp3_path = os.path.join(tmpdir, "input.mp3")
            ogg_path = os.path.join(tmpdir, "output.ogg")
            with open(mp3_path, "wb") as f:
                f.write(mp3_bytes)
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", mp3_path, "-c:a", "libopus", "-b:a", "64k", ogg_path],
                capture_output=True, timeout=60,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.decode(errors="replace"))
            with open(ogg_path, "rb") as f:
                return f.read()
    except Exception as e:
        log.warning("Conversione MP3→OGG fallita: %s", e)
    return None


# ── Helper: salva file in uploads/ ───────────────────────────────────

def _save_upload(filename: str, content: bytes) -> Path:
    dest = UPLOADS_DIR / filename
    dest.write_bytes(content)
    return dest


# ── /start ────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        await update.message.reply_text("⛔ Accesso non autorizzato.")
        return
    await update.message.reply_text("Attivato.")


# ── /reset ────────────────────────────────────────────────────────────

async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        await update.message.reply_text("⛔ Accesso non autorizzato.")
        return
    try:
        requests.post(f"{CIPHER_SERVER_URL}/reset", timeout=5)
        context.user_data.pop("pending_file", None)
        await update.message.reply_text("↺ Conversazione resettata.")
    except Exception as e:
        await update.message.reply_text(f"Errore reset: {e}")


async def cmd_stato(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mostra stato emotivo, obiettivi attivi e etica di Cipher."""
    if not is_authorized(update):
        await update.message.reply_text("⛔ Accesso non autorizzato.")
        return
    try:
        resp = requests.get(f"{CIPHER_SERVER_URL}/consciousness/status", timeout=10)
        data = resp.json()
        await update.message.reply_text(f"🧠 Stato Cipher:\n\n{data.get('status', 'Non disponibile')}")
    except Exception as e:
        await update.message.reply_text(f"Errore: {e}")


async def cmd_pensieri(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mostra gli ultimi pensieri di Cipher da thoughts.md."""
    if not is_authorized(update):
        await update.message.reply_text("⛔ Accesso non autorizzato.")
        return
    try:
        resp = requests.get(f"{CIPHER_SERVER_URL}/consciousness/thoughts", timeout=10)
        data = resp.json()
        thoughts = data.get("thoughts", "Nessun pensiero ancora.")
        # Telegram ha limite 4096 caratteri
        if len(thoughts) > 4000:
            thoughts = thoughts[-4000:]
        await update.message.reply_text(f"💭 Ultimi pensieri:\n\n{thoughts}")
    except Exception as e:
        await update.message.reply_text(f"Errore: {e}")


async def cmd_obiettivi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Mostra gli obiettivi attivi di Cipher."""
    if not is_authorized(update):
        await update.message.reply_text("⛔ Accesso non autorizzato.")
        return
    try:
        resp = requests.get(f"{CIPHER_SERVER_URL}/consciousness/goals", timeout=10)
        data = resp.json()
        goals = data.get("goals", "Nessun obiettivo attivo.")
        await update.message.reply_text(f"🎯 Obiettivi:\n\n{goals}")
    except Exception as e:
        await update.message.reply_text(f"Errore: {e}")


# ── Handler messaggi ──────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        await update.message.reply_text("⛔ Accesso non autorizzato.")
        return

    message = update.message
    chat_id = update.effective_chat.id

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    # ── FILE IN ATTESA DI ISTRUZIONE ──────────────────────────────────
    # Se c'è un file salvato in attesa, il prossimo messaggio è l'istruzione
    if message.text and context.user_data.get("pending_file"):
        instruction = message.text.strip()
        pending     = context.user_data.pop("pending_file")
        filename    = pending["filename"]

        # Passa al server con l'azione appropriata
        # Il server usa il FileEngine per gestire il file
        prompt = f"[FILE:{filename}] {instruction}"
        reply  = _ask_cipher(prompt)
        await message.reply_text(reply)
        return

    # ── TESTO ─────────────────────────────────────────────────────────
    if message.text:
        reply = _ask_cipher(message.text.strip())
        await message.reply_text(reply)
        return

    # ── VOICE / AUDIO ─────────────────────────────────────────────────
    if message.voice or message.audio:
        media = message.voice or message.audio
        try:
            raw        = _download_telegram_file(media.file_id)
            transcript = _transcribe_audio(raw)
        except Exception as e:
            await message.reply_text(f"❌ Errore trascrizione: {e}")
            return

        await message.reply_text(f"🎙️ _{transcript}_", parse_mode="Markdown")
        reply = _ask_cipher(transcript)
        await message.reply_text(reply)

        # Risposta vocale
        await context.bot.send_chat_action(chat_id=chat_id, action="record_voice")
        mp3 = _synthesize_tts(reply)
        if mp3:
            ogg = _mp3_to_ogg(mp3)
            if ogg:
                await message.reply_voice(voice=BytesIO(ogg))
        return

    # ── FOTO ──────────────────────────────────────────────────────────
    if message.photo:
        try:
            raw = _download_telegram_file(message.photo[-1].file_id)
        except Exception as e:
            await message.reply_text(f"❌ Errore download foto: {e}")
            return

        b64 = base64.standard_b64encode(raw).decode("utf-8")
        text = message.caption or "[foto]"
        reply = _ask_cipher(text, image_b64=b64, media_type="image/jpeg")
        await message.reply_text(reply)
        return

    # ── DOCUMENTO / FILE ──────────────────────────────────────────────
    if message.document:
        doc      = message.document
        filename = doc.file_name or f"file_{doc.file_id}"
        caption  = message.caption or ""

        log.info("File ricevuto: %s", filename)

        try:
            raw = _download_telegram_file(doc.file_id)
            _save_upload(filename, raw)
        except Exception as e:
            await message.reply_text(f"❌ Errore download file: {e}")
            return

        # Se c'è già una caption, usala come istruzione diretta
        if caption:
            prompt = f"[FILE:{filename}] {caption}"
            reply  = _ask_cipher(prompt)
            await message.reply_text(reply)
        else:
            # Nessuna istruzione — chiedi cosa fare
            context.user_data["pending_file"] = {"filename": filename}
            await message.reply_text(f"📎 Ho ricevuto {filename}. Cosa vuoi che faccia?")
        return

    # ── Tipo non gestito ──────────────────────────────────────────────
    await message.reply_text(
        "Tipo di messaggio non supportato. "
        "Puoi inviarmi: testo, vocali, foto o file."
    )


# ── Main ──────────────────────────────────────────────────────────────

def main() -> None:
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_BOT_TOKEN non impostato nel .env")
    if not ALLOWED_ID:
        raise ValueError("TELEGRAM_ALLOWED_ID non impostato nel .env")

    log.info("Avvio Cipher Telegram Bot (utente: %s)", ALLOWED_ID)

    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("reset", cmd_reset))
    application.add_handler(CommandHandler("stato", cmd_stato))
    application.add_handler(CommandHandler("pensieri", cmd_pensieri))
    application.add_handler(CommandHandler("obiettivi", cmd_obiettivi))
    application.add_handler(
        MessageHandler(
            (filters.TEXT | filters.VOICE | filters.AUDIO | filters.PHOTO | filters.Document.ALL)
            & ~filters.COMMAND,
            handle_message,
        )
    )

    log.info("Bot in ascolto...")
    application.run_polling()


if __name__ == "__main__":
    main()
