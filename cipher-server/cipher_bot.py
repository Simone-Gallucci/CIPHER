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

def _ask_cipher(text: str, image_b64: str | None = None, media_type: str = "image/jpeg", chat_id: str = "") -> str:
    payload: dict = {"message": text}
    if chat_id:
        payload["chat_id"] = chat_id
    if image_b64:
        payload["image_b64"] = image_b64
        payload["media_type"] = media_type
    try:
        resp = requests.post(
            f"{CIPHER_SERVER_URL}/chat",
            json=payload,
            headers={"X-Cipher-Token": Config.CIPHER_API_TOKEN},
            timeout=300,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("response") or data.get("message") or "⚠️ Nessuna risposta dal server. Riprova."
    except requests.exceptions.ConnectionError:
        return "⚠️ Server Cipher non raggiungibile."
    except requests.exceptions.Timeout:
        return "⚠️ Server Cipher non risponde (timeout)."
    except Exception as e:
        return "⚠️ Errore di comunicazione con il server."


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


# ── Helper: pulizia chat al reset ────────────────────────────────────

async def _clear_telegram_chat(bot, chat_id: int, user_data: dict) -> None:
    """Elimina tutti i messaggi tracciati nella sessione corrente.
    Silenzioso su errori — i messaggi utente non sono cancellabili nelle chat private."""
    msg_ids = user_data.pop("message_ids", [])
    for mid in msg_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=mid)
        except Exception:
            pass


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
        requests.post(f"{CIPHER_SERVER_URL}/reset", headers={"X-Cipher-Token": Config.CIPHER_API_TOKEN}, timeout=5)
        context.user_data.pop("pending_file", None)
        await _clear_telegram_chat(context.bot, update.effective_chat.id, context.user_data)
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
    """Chiede a Cipher a cosa sta pensando, passando per brain.think()."""
    if not is_authorized(update):
        await update.message.reply_text("⛔ Accesso non autorizzato.")
        return
    _cid = str(update.effective_chat.id)
    await context.bot.send_chat_action(update.effective_chat.id, action="typing")
    reply = _ask_cipher("a cosa stai pensando?", chat_id=_cid)
    await update.message.reply_text(reply)


async def cmd_obiettivi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Chiede a Cipher i suoi obiettivi, passando per brain.think()."""
    if not is_authorized(update):
        await update.message.reply_text("⛔ Accesso non autorizzato.")
        return
    _cid = str(update.effective_chat.id)
    await context.bot.send_chat_action(update.effective_chat.id, action="typing")
    reply = _ask_cipher("che obiettivi hai al momento?", chat_id=_cid)
    await update.message.reply_text(reply)


# ── Handler messaggi ──────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        await update.message.reply_text("⛔ Accesso non autorizzato.")
        return

    message = update.message
    chat_id = update.effective_chat.id
    _cid = str(chat_id)

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    # Traccia tutti i message ID per poterli cancellare al reset
    _msg_ids: list = context.user_data.setdefault("message_ids", [])
    _msg_ids.append(message.message_id)

    # ── FILE IN ATTESA DI ISTRUZIONE ──────────────────────────────────
    # Se c'è un file salvato in attesa, il prossimo messaggio è l'istruzione
    if message.text and context.user_data.get("pending_file"):
        instruction = message.text.strip()
        pending     = context.user_data.pop("pending_file")
        filename    = pending["filename"]

        # Passa al server con l'azione appropriata
        # Il server usa il FileEngine per gestire il file
        prompt = f"[FILE:{filename}] {instruction}"
        reply  = _ask_cipher(prompt, chat_id=_cid)
        sent = await message.reply_text(reply)
        _msg_ids.append(sent.message_id)
        return

    # ── TESTO ─────────────────────────────────────────────────────────
    if message.text:
        reply = _ask_cipher(message.text.strip(), chat_id=_cid)
        if reply.startswith("__RESET__"):
            reply = reply[len("__RESET__"):]
            await _clear_telegram_chat(context.bot, chat_id, context.user_data)
            await message.reply_text(reply)
            return
        sent = await message.reply_text(reply)
        _msg_ids.append(sent.message_id)
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

        t_sent = await message.reply_text(f"🎙️ _{transcript}_", parse_mode="Markdown")
        _msg_ids.append(t_sent.message_id)
        reply = _ask_cipher(transcript, chat_id=_cid)
        r_sent = await message.reply_text(reply)
        _msg_ids.append(r_sent.message_id)

        # Risposta vocale
        await context.bot.send_chat_action(chat_id=chat_id, action="record_voice")
        mp3 = _synthesize_tts(reply)
        if mp3:
            ogg = _mp3_to_ogg(mp3)
            if ogg:
                v_sent = await message.reply_voice(voice=BytesIO(ogg))
                _msg_ids.append(v_sent.message_id)
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
        reply = _ask_cipher(text, image_b64=b64, media_type="image/jpeg", chat_id=_cid)
        sent = await message.reply_text(reply)
        _msg_ids.append(sent.message_id)
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
            reply  = _ask_cipher(prompt, chat_id=_cid)
            sent = await message.reply_text(reply)
            _msg_ids.append(sent.message_id)
        else:
            # Nessuna istruzione — chiedi cosa fare
            context.user_data["pending_file"] = {"filename": filename}
            sent = await message.reply_text(f"📎 Ho ricevuto {filename}. Cosa vuoi che faccia?")
            _msg_ids.append(sent.message_id)
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
