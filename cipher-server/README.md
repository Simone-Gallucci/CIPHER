# Cipher — AI Companion

Cipher is an AI companion with persistent memory, autonomous reflection, self-generated goals, and proactive messaging via Telegram. Built on Python/Flask with Claude LLM (via OpenRouter or Anthropic direct), it maintains a growing relationship with its user through genuine conversational signals. Runs on Linux (Raspberry Pi or VPS) with 4 systemd services.

## Prerequisites

- Python 3.11+
- systemd (Linux)
- Anthropic or OpenRouter API key
- Telegram bot token (from @BotFather)
- Google OAuth2 credentials (for Calendar/Gmail)
- Tailscale (optional, for HTTPS exposure)
- ffmpeg (for voice message transcoding)

## Setup

### 1. Clone and environment

```bash
git clone https://github.com/Simone-Gallucci/CIPHER.git
cd CIPHER/cipher-server
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configuration

```bash
cp .env.example .env
chmod 600 .env
# Edit .env with your API keys, Telegram token, etc.
```

Key variables: `LLM_PROVIDER`, `OPENROUTER_API_KEY` or `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_ID`, `CIPHER_API_TOKEN`.

See `.env.example` for the full list with descriptions.

### 3. Google OAuth2 (optional)

Place your `credentials.json` in `secrets/`. On first run, the OAuth2 flow will generate `secrets/token.json`. Scopes: Calendar + Gmail (modify).

### 4. Vosk models (optional, for voice input)

Download from [alphacephei.com/vosk/models](https://alphacephei.com/vosk/models):
- Italian: `vosk-model-it-0.22` → extract to `models/`
- English: `vosk-model-en-us-0.22` → extract to `models/`

### 5. Migration (if upgrading from pre-hardening version)

```bash
python scripts/migrate_home.py
python scripts/migrate_memory.py
```

These scripts are idempotent and create automatic backups.

### 6. Systemd services

```bash
sudo bash setup.sh
sudo systemctl start cipher.service cipher-telegram.service cipher-memory.service
```

### 7. Verify

```bash
curl http://127.0.0.1:5000/health
```

Expected: `{"status": "ok", "model": "...", "confidence": ..., ...}`

---

## Architecture

### Layers

- **Conversational**: Telegram/Web/CLI → `server.py` → `Brain.think()` → Claude Sonnet → response
- **Autonomous**: `ConsciousnessLoop` (daemon thread, ~60s cycle) → self-reflection, goals, check-ins, morning brief → Claude Haiku/Sonnet
- **Memory**: persistent profile, conversations, episodic memory, emotional log, pattern learning → JSON/MD files in `memory/user_<id>/`

### Security modules

| Module | Role | Step | Audit log |
|---|---|---|---|
| `shell_guard.py` | Whitelist-based shell execution (replaces `shell=True`) | 1 | `logs/shell_audit.log` |
| `path_guard.py` | Path traversal prevention, per-user home isolation | 2 | `logs/file_audit.log` |
| `prompt_sanitizer.py` | Prompt injection detection (33+ patterns), memory field sanitization, untrusted data wrapping | 3 | `logs/injection_audit.log` |
| `auth.py` | Centralized user identity, per-user directory routing | 2, 4 | — |
| `admin_lockout.py` | Persistent admin lockout (5 attempts → 30 min) | 5 | `logs/admin_audit.log` |
| `message_rate_limiter.py` | Per-user message rate limiting (10/min, 60/hour) | 5 | — |

### Directory structure

```
cipher-server/
├── server.py              # Flask entry point
├── cipher_bot.py          # Telegram bot (separate process)
├── main.py                # CLI entry point
├── memory_worker.py       # Profile consolidation (hourly)
├── config.py              # Centralized configuration
├── .env                   # Secrets (NOT committed)
├── .env.example           # Template with all variables
├── requirements.txt
├── comportamento/         # Static personality files (loaded into system prompt)
├── config/                # Dev protocol
├── data/                  # Permanent data (survives Tabula Rasa)
│   ├── admin.json         # Permanent bond (PBKDF2-SHA256)
│   ├── changelog.json     # Backup log
│   ├── patterns.json      # Behavioral patterns
│   ├── lockouts.json      # Admin lockout state
│   └── rate_limits.json   # Rate limit state
├── home/                  # Per-user home directories
│   └── user_simone/       # User files, uploads
├── memory/                # Per-user memory (resettable via Tabula Rasa)
│   └── user_simone/       # Profile, conversations, goals, state
├── logs/                  # Audit logs (JSONL, auto-rotation 5MB x 10)
├── models/                # Vosk STT models
├── modules/               # Core logic
├── scripts/               # Migration utilities
├── secrets/               # Google OAuth2 credentials (NOT committed)
└── web/                   # Dashboard (single HTML file)
    ├── index.html
    └── static/logo.jpg
```

---

## Deploy

```bash
git pull
sudo systemctl restart cipher.service cipher-telegram.service cipher-memory.service
```

**Important**: `git commit` ≠ deploy. Always restart services after pulling changes. Verify services are running:

```bash
sudo systemctl status cipher.service cipher-telegram.service cipher-memory.service
curl http://127.0.0.1:5000/health
```

---

## Debug

```bash
# Real-time logs
sudo journalctl -u cipher.service -f
sudo journalctl -u cipher-telegram.service -f

# Recent errors
sudo journalctl -u cipher.service --since "10 min ago" | grep -iE "error|traceback"

# Syntax check a module
venv/bin/python3 -m py_compile modules/brain.py

# Audit logs
tail -20 logs/shell_audit.log | python3 -m json.tool --no-ensure-ascii
tail -20 logs/file_audit.log | python3 -m json.tool --no-ensure-ascii
tail -20 logs/injection_audit.log | python3 -m json.tool --no-ensure-ascii
tail -20 logs/admin_audit.log | python3 -m json.tool --no-ensure-ascii

# Test chat endpoint
curl -s -X POST http://127.0.0.1:5000/chat \
  -H "Content-Type: application/json" \
  -H "X-Cipher-Token: YOUR_TOKEN" \
  -d '{"message":"test","chat_id":"debug"}' | python3 -m json.tool
```

---

## Security

Cipher implements defense-in-depth across 5 hardening steps:

- **Shell execution** (Step 1): All subprocess calls use `ShellGuard` with a whitelist of ~30 allowed binaries. No `shell=True`, no environment variable propagation, pipe support between whitelisted commands only.
- **Path traversal** (Step 2): All file operations validate paths via `path_guard.py` using `Path.resolve()` + `relative_to()`. Per-user home directories (`home/user_<id>/`) with `0o700` permissions.
- **Prompt injection** (Step 3): 33+ regex patterns (EN/IT) detect injection attempts in memory extraction and file processing. Untrusted data wrapped in XML-like tags before system prompt injection. Leet speak normalization.
- **Memory isolation** (Step 4): Per-user memory directories (`memory/user_<id>/`) with `0o700` permissions. All memory files written with `0o600`. Atomic writes via tmp+rename.
- **Rate limiting** (Step 5): Persistent admin lockout (5 failed attempts → 30 min block). Per-user message rate limiting (10/min, 60/hour) applied before any LLM call.
- **Audit logging**: All security-relevant events logged to JSONL files in `logs/` with automatic rotation (5MB x 10 files).
- **API authentication**: All endpoints (except `/health` and `/web`) require `X-Cipher-Token` header. HTTP rate limiting at 30 req/min per IP.
