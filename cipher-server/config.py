"""
config.py – Configurazione centralizzata di Cipher
Legge tutto dal file .env e lo espone come oggetto Config.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)


class Config:
    # ── OpenRouter ───────────────────────────────────────────
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
    OPENROUTER_MODEL: str   = os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-4-6")
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"

    # ── Telegram ─────────────────────────────────────────────────────────
    TELEGRAM_BOT_TOKEN:  str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_ALLOWED_ID: int = int(os.getenv("TELEGRAM_ALLOWED_ID", "0"))

    # ── Modalità input ───────────────────────────────────────
    INPUT_MODE: str = os.getenv("INPUT_MODE", "both").lower()

    # ── Audio ────────────────────────────────────────────────
    SAMPLE_RATE: int       = 16000
    CHANNELS: int          = 1
    BLOCK_SIZE: int        = 4000
    MIC_DEVICE_INDEX: int  = int(os.getenv("MIC_DEVICE_INDEX", -1))
    SILENCE_TIMEOUT: float = float(os.getenv("SILENCE_TIMEOUT", 2.0))

    # ── Vosk ─────────────────────────────────────────────────
    VOSK_MODEL_PATH: str = os.getenv(
        "VOSK_MODEL_PATH",
        str(Path(__file__).parent / "models" / "vosk-model-it-0.22")

    )

    # ── Wake word / lingua ───────────────────────────────────
    WAKE_WORD: str = os.getenv("WAKE_WORD", "cipher").lower()
    WAKE_WORDS: list = [
        w.strip().lower()
        for w in os.getenv("WAKE_WORDS", "cipher,jarvis,ehi,ci sei,ehi amico").split(",")
        if w.strip()
]
# Modello separato per wake word inglesi (cipher, jarvis)
    VOSK_WAKE_MODEL_PATH: str = os.getenv(
        "VOSK_WAKE_MODEL_PATH",
        str(Path(__file__).parent / "models" / "vosk-model-en-us-0.22")
    )
    LANGUAGE: str  = os.getenv("LANGUAGE", "it")
    # ── ElevenLabs TTS ───────────────────────────────────
    ELEVENLABS_API_KEY:  str = os.getenv("ELEVENLABS_API_KEY", "")
    ELEVENLABS_VOICE_ID: str = os.getenv("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")
    GREEN_API_INSTANCE_ID: str = os.getenv("GREEN_API_INSTANCE_ID", "")
    GREEN_API_TOKEN:       str = os.getenv("GREEN_API_TOKEN", "")

    # ── Google OAuth2 ─────────────────────────────────────
    GOOGLE_CREDENTIALS_FILE: str = os.getenv(
        "GOOGLE_CREDENTIALS_FILE",
        str(Path(__file__).parent / "credentials.json")
    )
    GOOGLE_TOKEN_FILE: str = os.getenv(
        "GOOGLE_TOKEN_FILE",
        str(Path(__file__).parent / "token.json")
    )
    GOOGLE_SCOPES: list = [
        "https://www.googleapis.com/auth/calendar",
        "https://www.googleapis.com/auth/gmail.modify",
    ]
  
    # ── Paths ────────────────────────────────────────────────
    BASE_DIR:   Path = Path(__file__).parent
    HOME_DIR:   Path = BASE_DIR / "home"
    MEMORY_DIR: Path = BASE_DIR / "memory"
    MODELS_DIR: Path = BASE_DIR / "models"

    # ── Limiti conversazione ─────────────────────────────────
    MAX_HISTORY_MESSAGES: int = 20

    @classmethod
    def validate(cls) -> list[str]:
        """Ritorna lista di errori critici. Controlla solo ciò che serve alla modalità scelta."""
        errors = []

        if not cls.OPENROUTER_API_KEY or cls.OPENROUTER_API_KEY.startswith("sk-or-..."):
            errors.append(
                "OPENROUTER_API_KEY non impostata nel file .env\n"
                "  → Ottieni la key su https://openrouter.ai/keys"
            )

        if cls.INPUT_MODE in ("voice", "both"):
            if not Path(cls.VOSK_MODEL_PATH).exists():
                errors.append(
                    f"Modello Vosk non trovato: {cls.VOSK_MODEL_PATH}\n"
                    f"  → Scaricalo da https://alphacephei.com/vosk/models\n"
                    f"  → Estrai in: {cls.MODELS_DIR}\n"
                    f"  → Oppure usa --mode text per avviare senza microfono"
                )

        if cls.INPUT_MODE not in ("text", "voice", "both"):
            errors.append(f"INPUT_MODE non valido: '{cls.INPUT_MODE}'. Usa: text, voice, both")

        return errors


# Crea le directory necessarie al primo avvio
for _d in (Config.HOME_DIR, Config.MEMORY_DIR, Config.MODELS_DIR):
    _d.mkdir(parents=True, exist_ok=True)
