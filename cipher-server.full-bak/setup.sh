#!/bin/bash
# setup.sh – Installa e configura Cipher da zero

set -e

# ── Colori (disponibili subito, anche prima del venv) ─────────────────
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BOLD='\033[1m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓ $1${NC}"; }
warn() { echo -e "  ${YELLOW}⚠ $1${NC}"; }
info() { echo -e "  ${CYAN}→ $1${NC}"; }
err()  { echo -e "  ${RED}✗ $1${NC}"; }

# ── 0. Dipendenze sistema (prima del venv) ────────────────────────────
if [ -z "$VIRTUAL_ENV" ]; then
    echo -e "${CYAN}  Installazione dipendenze sistema...${NC}"
    sudo apt-get update -q
    sudo apt-get install -y \
        python3 python3-pip python3-venv \
        espeak-ng espeak-ng-data \
        portaudio19-dev libsndfile1 \
        mpg123 ffmpeg wget unzip curl \
        --no-install-recommends -q
    ok "Dipendenze sistema installate"

    # Crea e attiva il venv, poi ri-esegui lo script dentro di esso
    if [ ! -d "venv" ]; then
        python3 -m venv venv
        ok "Venv creato"
    fi
    exec bash -c "source venv/bin/activate && exec bash \"$0\" $*"
fi

clear
echo -e "${CYAN}"
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║         C I P H E R  –  Setup               ║"
echo "  ║         Assistente AI personale              ║"
echo "  ╚══════════════════════════════════════════════╝"
echo -e "${NC}"

# ── Utility ───────────────────────────────────────────────────────────
ask() {
    local prompt="$1" var="$2" default="$3"
    echo -ne "${CYAN}  → ${prompt}${NC}"
    [ -n "$default" ] && echo -ne " ${YELLOW}[${default}]${NC}"
    echo -ne ": "
    read -r input
    eval "$var=\"${input:-$default}\""
}

ask_secret() {
    local prompt="$1" var="$2"
    echo -ne "${CYAN}  → ${prompt}${NC}: "
    read -rs input
    echo ""
    eval "$var=\"$input\""
}

section() { echo -e "\n${CYAN}${BOLD}── $1 ──────────────────────────────────────────${NC}"; }
ok()      { echo -e "  ${GREEN}✓ $1${NC}"; }
warn()    { echo -e "  ${YELLOW}⚠ $1${NC}"; }
info()    { echo -e "  ${CYAN}→ $1${NC}"; }
err()     { echo -e "  ${RED}✗ $1${NC}"; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── 1. Dipendenze Python ──────────────────────────────────────────────
section "1/7  Dipendenze Python"
pip install --upgrade pip -q
pip install -r requirements.txt -q
ok "Dipendenze Python installate"

# ── 3. Playwright (browser automation) ───────────────────────────────
section "2/7  Playwright browser"
if python -c "from playwright.sync_api import sync_playwright" 2>/dev/null; then
    info "Installo browser Playwright (Chromium)..."
    playwright install chromium --with-deps -q
    ok "Playwright pronto"
else
    err "Playwright non installato — controlla requirements.txt"
fi

# ── 4. Modelli Vosk ───────────────────────────────────────────────────
section "3/7  Modelli Vosk (STT offline)"
mkdir -p models

MODEL_IT="vosk-model-small-it-0.22"
if [ ! -d "models/${MODEL_IT}" ]; then
    info "Scarico modello italiano (~40MB)..."
    wget -q --show-progress "https://alphacephei.com/vosk/models/${MODEL_IT}.zip" -O "models/${MODEL_IT}.zip"
    unzip -q "models/${MODEL_IT}.zip" -d models/ && rm "models/${MODEL_IT}.zip"
    ok "Modello italiano scaricato"
else
    warn "Modello IT già presente"
fi

MODEL_EN="vosk-model-small-en-us-0.15"
if [ ! -d "models/${MODEL_EN}" ]; then
    info "Scarico modello inglese per wake word (~40MB)..."
    wget -q --show-progress "https://alphacephei.com/vosk/models/${MODEL_EN}.zip" -O "models/${MODEL_EN}.zip"
    unzip -q "models/${MODEL_EN}.zip" -d models/ && rm "models/${MODEL_EN}.zip"
    ok "Modello inglese scaricato"
else
    warn "Modello EN già presente"
fi

# ── 5. Configurazione .env ────────────────────────────────────────────
section "4/7  Configurazione API Keys"

if [ -f ".env" ]; then
    warn ".env già esistente — leggo i valori esistenti"
    OPENROUTER_API_KEY=$(grep "^OPENROUTER_API_KEY=" .env | cut -d'=' -f2-)
    ELEVENLABS_API_KEY=$(grep "^ELEVENLABS_API_KEY=" .env | cut -d'=' -f2-)
    ELEVENLABS_VOICE_ID=$(grep "^ELEVENLABS_VOICE_ID=" .env | cut -d'=' -f2-)
    TELEGRAM_BOT_TOKEN=$(grep "^TELEGRAM_BOT_TOKEN=" .env | cut -d'=' -f2-)
    TELEGRAM_ALLOWED_ID=$(grep "^TELEGRAM_ALLOWED_ID=" .env | cut -d'=' -f2-)
    GREEN_API_INSTANCE_ID=$(grep "^GREEN_API_INSTANCE_ID=" .env | cut -d'=' -f2-)
    GREEN_API_TOKEN=$(grep "^GREEN_API_TOKEN=" .env | cut -d'=' -f2-)
    SERVER_PORT=$(grep "^SERVER_PORT=" .env | cut -d'=' -f2-)
    SERVER_PORT="${SERVER_PORT:-5000}"
    ok "Keys caricate da .env esistente"
else
    # OpenRouter (obbligatorio)
    echo -e "\n  ${BOLD}OpenRouter API Key${NC} (obbligatoria — ottienila su openrouter.ai/keys)"
    ask_secret "OPENROUTER_API_KEY" OPENROUTER_API_KEY

    # ElevenLabs (opzionale)
    echo ""
    echo -ne "  ${CYAN}→ Configurare ElevenLabs TTS? [s/N]${NC}: "
    read -r use_elevenlabs
    if [[ "$use_elevenlabs" =~ ^[sS]$ ]]; then
        ask_secret "ELEVENLABS_API_KEY" ELEVENLABS_API_KEY
        ask "ELEVENLABS_VOICE_ID" ELEVENLABS_VOICE_ID "JBFqnCBsd6RMkjVDRZzb"
    else
        ELEVENLABS_API_KEY=""
        ELEVENLABS_VOICE_ID="JBFqnCBsd6RMkjVDRZzb"
        warn "ElevenLabs saltato"
    fi

    # Telegram (opzionale)
    echo ""
    echo -ne "  ${CYAN}→ Configurare Telegram Bot? [s/N]${NC}: "
    read -r use_telegram
    if [[ "$use_telegram" =~ ^[sS]$ ]]; then
        ask_secret "TELEGRAM_BOT_TOKEN" TELEGRAM_BOT_TOKEN
        ask "Il tuo Telegram user ID" TELEGRAM_ALLOWED_ID ""
    else
        TELEGRAM_BOT_TOKEN=""
        TELEGRAM_ALLOWED_ID=""
        warn "Telegram saltato"
    fi

    # Green API / WhatsApp (opzionale)
    echo ""
    echo -ne "  ${CYAN}→ Configurare WhatsApp (Green API)? [s/N]${NC}: "
    read -r use_green
    if [[ "$use_green" =~ ^[sS]$ ]]; then
        ask "GREEN_API_INSTANCE_ID" GREEN_API_INSTANCE_ID ""
        ask_secret "GREEN_API_TOKEN" GREEN_API_TOKEN
    else
        GREEN_API_INSTANCE_ID=""
        GREEN_API_TOKEN=""
        warn "Green API saltato"
    fi

    # Server port
    ask "Porta del server HTTP" SERVER_PORT "5000"

    cat > .env << ENVEOF
# Cipher – configurazione generata da setup.sh

# OpenRouter
OPENROUTER_API_KEY=${OPENROUTER_API_KEY}
OPENROUTER_MODEL=anthropic/claude-sonnet-4-6

# Modalità input (viene aggiornata sotto)
INPUT_MODE=text

# Audio
MIC_DEVICE_INDEX=-1
SILENCE_TIMEOUT=2.0

# Vosk (STT)
VOSK_MODEL_PATH=./models/${MODEL_IT}
VOSK_WAKE_MODEL_PATH=./models/${MODEL_EN}

# Lingua e wake words
LANGUAGE=it
WAKE_WORD=cipher
WAKE_WORDS=cipher,jarvis,ehi,ci sei,ehi amico

# ElevenLabs (TTS)
ELEVENLABS_API_KEY=${ELEVENLABS_API_KEY}
ELEVENLABS_VOICE_ID=${ELEVENLABS_VOICE_ID}

# Telegram Bot
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
TELEGRAM_ALLOWED_ID=${TELEGRAM_ALLOWED_ID}

# WhatsApp (Green API)
GREEN_API_INSTANCE_ID=${GREEN_API_INSTANCE_ID}
GREEN_API_TOKEN=${GREEN_API_TOKEN}

# Webhook WhatsApp diretto (opzionale)
WHATSAPP_API_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_VERIFY_TOKEN=cipher_webhook_secret
WEBHOOK_BASE_URL=
WEBHOOK_PORT=${SERVER_PORT}

# Server
SERVER_PORT=${SERVER_PORT}
CIPHER_SERVER_URL=http://localhost:${SERVER_PORT}
ENVEOF
    ok ".env creato"
fi

# ── 6. Profilo utente ─────────────────────────────────────────────────
section "5/7  Profilo utente (memoria)"
mkdir -p memory/conversations

if [ -f "memory/profile.json" ]; then
    warn "Profilo già esistente — skippo"
else
    echo -e "\n  Questi dati vengono salvati nella memoria di Cipher.\n"
    ask "Il tuo nome"                       USER_NOME      ""
    ask "Il tuo cognome"                    USER_COGNOME   ""
    ask "Data di nascita"                   USER_NASCITA   ""
    ask "Interessi (es: tech,auto,musica)"  USER_INTERESSI ""

    cat > memory/profile.json << PROFILEEOF
{
  "personal": {
    "nome": "${USER_NOME}",
    "cognome": "${USER_COGNOME}",
    "data_di_nascita": "${USER_NASCITA}"
  },
  "preferences": {
    "interessi": "${USER_INTERESSI}"
  },
  "facts": [],
  "updated_at": "$(date -Iseconds)"
}
PROFILEEOF
    ok "Profilo salvato in memory/profile.json"
fi

# ── 7. Modalità di avvio ──────────────────────────────────────────────
section "6/7  Modalità di avvio"
echo ""
echo -e "  ${CYAN}1${NC}) ${BOLD}Full mode${NC}     — Testo + Microfono + Voce TTS"
echo -e "  ${CYAN}2${NC}) ${BOLD}Testo + Voce${NC}  — Tastiera + risposta vocale (no microfono)"
echo -e "  ${CYAN}3${NC}) ${BOLD}Solo testo${NC}    — Nessun audio"
echo ""
echo -ne "  ${CYAN}→ Scelta [1/2/3]${NC}: "
read -r mode_choice

case "$mode_choice" in
    1) INPUT_MODE="both"; TTS_ENABLED="true";  ok "Modalità: Full" ;;
    2) INPUT_MODE="text"; TTS_ENABLED="true";  ok "Modalità: Testo + Voce" ;;
    3) INPUT_MODE="text"; TTS_ENABLED="false"; ok "Modalità: Solo testo" ;;
    *) INPUT_MODE="text"; TTS_ENABLED="false"; warn "Scelta non valida, uso Solo testo" ;;
esac

sed -i "s/^INPUT_MODE=.*/INPUT_MODE=${INPUT_MODE}/" .env
ok ".env aggiornato con INPUT_MODE=${INPUT_MODE}"

# ── 8. Systemd (avvio automatico) ────────────────────────────────────
section "7/7  Avvio automatico (systemd)"
echo ""
echo -ne "  ${CYAN}→ Installare Cipher come servizio systemd (avvio automatico al boot)? [s/N]${NC}: "
read -r install_service

if [[ "$install_service" =~ ^[sS]$ ]]; then
    SERVICE_USER="${USER:-$(whoami)}"
    VENV_PYTHON="${SCRIPT_DIR}/venv/bin/python"

    # Servizio per main.py (assistente vocale/testo)
    sudo tee /etc/systemd/system/cipher.service > /dev/null << SERVICEEOF
[Unit]
Description=Cipher AI Assistant
After=network.target sound.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${SCRIPT_DIR}
ExecStart=${VENV_PYTHON} ${SCRIPT_DIR}/main.py
Restart=on-failure
RestartSec=5
EnvironmentFile=${SCRIPT_DIR}/.env

[Install]
WantedBy=multi-user.target
SERVICEEOF

    # Servizio per server.py (API HTTP + webhook)
    sudo tee /etc/systemd/system/cipher-server.service > /dev/null << SERVICEEOF
[Unit]
Description=Cipher HTTP Server
After=network.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${SCRIPT_DIR}
ExecStart=${VENV_PYTHON} ${SCRIPT_DIR}/server.py
Restart=on-failure
RestartSec=5
EnvironmentFile=${SCRIPT_DIR}/.env

[Install]
WantedBy=multi-user.target
SERVICEEOF

    sudo systemctl daemon-reload
    ok "Servizi systemd installati"

    echo ""
    echo -ne "  ${CYAN}→ Abilitare i servizi all'avvio del sistema? [s/N]${NC}: "
    read -r enable_boot
    if [[ "$enable_boot" =~ ^[sS]$ ]]; then
        sudo systemctl enable cipher.service
        sudo systemctl enable cipher-server.service
        ok "Servizi abilitati al boot"
    fi
fi

# ── Riepilogo finale ──────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}"
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║         Setup completato!                    ║"
echo "  ╚══════════════════════════════════════════════╝"
echo -e "${NC}"
echo -e "  Dir        : ${CYAN}${SCRIPT_DIR}${NC}"
echo -e "  Modalità   : ${CYAN}${INPUT_MODE}${NC}"
echo -e "  TTS        : ${CYAN}${TTS_ENABLED}${NC}"
echo ""
echo -e "${BOLD}  ── Comandi manuali ─────────────────────────────────${NC}"
echo -e "  Assistente : ${CYAN}source venv/bin/activate && python main.py${NC}"
echo -e "  Server     : ${CYAN}source venv/bin/activate && python server.py${NC}"
echo -e "  Telegram   : ${CYAN}source venv/bin/activate && python cipher_bot.py${NC}"
echo ""

if [[ "$install_service" =~ ^[sS]$ ]]; then
    echo -e "${BOLD}  ── Gestione servizi systemd ─────────────────────────${NC}"
    echo -e "  Avvia assistente : ${CYAN}sudo systemctl start cipher${NC}"
    echo -e "  Avvia server     : ${CYAN}sudo systemctl start cipher-server${NC}"
    echo -e "  Stato            : ${CYAN}sudo systemctl status cipher cipher-server${NC}"
    echo -e "  Log live         : ${CYAN}journalctl -fu cipher${NC}"
    echo -e "  Ferma tutto      : ${CYAN}sudo systemctl stop cipher cipher-server${NC}"
    echo ""
fi

echo -ne "  ${CYAN}→ Avviare Cipher adesso? [S/n]${NC}: "
read -r avvia

if [[ ! "$avvia" =~ ^[nN]$ ]]; then
    echo ""
    ok "Avvio Cipher..."
    echo ""
    exec python main.py
fi
