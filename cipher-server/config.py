"""
config.py – Configurazione centralizzata di Cipher
Legge tutto dal file .env e lo espone come oggetto Config.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)


class Config:
    # ── LLM Provider ─────────────────────────────────────────
    # Cambia LLM_PROVIDER nel .env per switchare provider:
    #   LLM_PROVIDER=openrouter  → usa OpenRouter (nessun rate limit TPM)
    #   LLM_PROVIDER=anthropic   → usa Anthropic diretto (Tier 2/3)
    _provider: str = os.getenv("LLM_PROVIDER", "openrouter").lower()

    OPENROUTER_API_KEY: str  = os.getenv("ANTHROPIC_API_KEY") if _provider == "anthropic" else os.getenv("OPENROUTER_API_KEY", "")
    OPENROUTER_BASE_URL: str = "https://api.anthropic.com/v1" if _provider == "anthropic" else "https://openrouter.ai/api/v1"
    _model_raw: str          = os.getenv("OPENROUTER_MODEL", "claude-sonnet-4-6" if _provider == "anthropic" else "anthropic/claude-sonnet-4-6")
    OPENROUTER_MODEL: str    = _model_raw.replace("anthropic/", "") if _provider == "anthropic" else _model_raw

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

    # ── Modello background (task silenti: estrazione, classificazione) ──
    # Default = Haiku (più leggero e veloce per operazioni interne)
    _bg_model_raw: str    = os.getenv("BACKGROUND_MODEL", "claude-haiku-4-5-20251001" if _provider == "anthropic" else "anthropic/claude-haiku-4-5")
    BACKGROUND_MODEL: str = _bg_model_raw.replace("anthropic/", "") if _provider == "anthropic" else _bg_model_raw

    # ── Modello conversazionale (risposte dirette — routing automatico) ──
    # Default = Haiku. Scala a OPENROUTER_MODEL se messaggio tecnico o lungo.
    _conv_model_raw: str    = os.getenv("CONVERSATION_MODEL", "claude-haiku-4-5-20251001" if _provider == "anthropic" else "anthropic/claude-haiku-4-5")
    CONVERSATION_MODEL: str = _conv_model_raw.replace("anthropic/", "") if _provider == "anthropic" else _conv_model_raw

    # ── API Auth ──────────────────────────────────────────
    # Token per autenticare le richieste all'API Flask.
    # Se vuoto → auth disabilitata (utile per ambienti interni).
    CIPHER_API_TOKEN: str = os.getenv("CIPHER_API_TOKEN", "")

    # ── Google OAuth2 ─────────────────────────────────────
    GOOGLE_CREDENTIALS_FILE: str = os.getenv(
        "GOOGLE_CREDENTIALS_FILE",
        str(Path(__file__).parent / "secrets" / "credentials.json")
    )
    GOOGLE_TOKEN_FILE: str = os.getenv(
        "GOOGLE_TOKEN_FILE",
        str(Path(__file__).parent / "secrets" / "token.json")
    )
    GOOGLE_SCOPES: list = [
        "https://www.googleapis.com/auth/calendar",
        "https://www.googleapis.com/auth/gmail.modify",  # Gmail: lettura + invio — SOLO su richiesta esplicita
    ]
  
    # ── Paths ────────────────────────────────────────────────
    BASE_DIR:   Path = Path(__file__).parent
    HOME_DIR:   Path = BASE_DIR / "home"
    MEMORY_DIR: Path = BASE_DIR / "memory"
    MODELS_DIR: Path = BASE_DIR / "models"
    DATA_DIR:   Path = BASE_DIR / "data"     # permanente — mai resettato

    # ── Limiti conversazione ─────────────────────────────────
    MAX_HISTORY_MESSAGES: int = 10

    # ── Compleanno utente (configurabile in .env) ────────────
    BIRTHDAY_DAY:   int = int(os.getenv("BIRTHDAY_DAY",   "0"))
    BIRTHDAY_MONTH: int = int(os.getenv("BIRTHDAY_MONTH", "0"))

    # ── Feature flags ────────────────────────────────────────
    CONSCIOUSNESS_ENABLED: bool = os.getenv("CONSCIOUSNESS_ENABLED", "true").lower() != "false"

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
for _d in (Config.HOME_DIR, Config.MEMORY_DIR, Config.MODELS_DIR, Config.DATA_DIR):
    _d.mkdir(parents=True, exist_ok=True)
