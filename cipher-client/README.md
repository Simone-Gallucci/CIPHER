# cipher-client

Client per Cipher — interfaccia voce/testo che si connette a `cipher-server` via rete (Tailscale).
Funziona su **Linux** (Debian/Ubuntu/Parrot) e **Android** (Termux).

---

## Indice

- [Prerequisiti](#prerequisiti)
- [Installazione](#installazione)
- [Configurazione](#configurazione)
- [Avvio](#avvio)
- [Comandi speciali](#comandi-speciali)
- [Servizio systemd](#servizio-systemd)
- [Struttura directory](#struttura-directory)

---

## Prerequisiti

- Python 3
- `mpg123`, `ffmpeg` (installati automaticamente da `install.sh`)
- `cipher-server` raggiungibile in rete
- **API key ElevenLabs** (opzionale — senza, il TTS è disabilitato)

---

## Installazione

```bash
chmod +x install.sh && ./install.sh
```

Lo script fa in automatico:
1. Installa le dipendenze di sistema (`mpg123`, `ffmpeg`, `portaudio19-dev`, ecc.)
2. Crea un virtualenv e installa i pacchetti Python (`requests`, `rich`, `elevenlabs`, `sounddevice`, ecc.)
3. Guida la configurazione del `.env`
4. Opzionalmente installa un servizio systemd per l'avvio automatico

### Installazione manuale dipendenze

```bash
# Linux
sudo apt install python3 python3-venv mpg123 ffmpeg portaudio19-dev libsndfile1

# Termux (Android)
pkg install python mpg123 termux-api ffmpeg

# Pacchetti Python
pip install requests rich python-dotenv elevenlabs sounddevice
```

---

## Configurazione

Creare un file `.env` nella root del progetto:

```env
# Server Cipher (Tailscale o IP locale)
CIPHER_SERVER_URL=http://<IP>:5000

# ElevenLabs TTS (opzionale — senza, il client funziona senza voce)
ELEVENLABS_API_KEY=
ELEVENLABS_VOICE_ID=JBFqnCBsd6RMkjVDRZzb

# Microfono (opzionale)
MIC_DEVICE_INDEX=-1          # -1 = dispositivo default
WAKE_WORDS=cipher,jarvis,ehi,ci sei
```

---

## Avvio

```bash
./run_client.sh                               # attiva venv e avvia
source venv/bin/activate
python cipher_client.py
python cipher_client.py --server http://IP:5000   # server custom
python cipher_client.py --no-tts                  # disabilita TTS
```

All'avvio chiede la modalità:
1. **Full** — tastiera + microfono + risposte vocali
2. **Testo + voce** — tastiera + risposte TTS
3. **Solo testo** — nessun audio

---

## Comandi speciali

| Comando | Effetto |
|---|---|
| `reset` | Resetta la conversazione sul server |
| `esci` / `exit` / `quit` | Chiude il client |

---

## Servizio systemd

Se hai scelto di installarlo durante `install.sh`:

```bash
sudo systemctl start cipher-client    # avvia
sudo systemctl status cipher-client   # stato
journalctl -fu cipher-client          # log in tempo reale
sudo systemctl stop cipher-client     # ferma
```

---

## Struttura directory

**`cipher_client.py`** — client principale: gestisce connessione al server, input tastiera/microfono, TTS e wake word. **`install.sh`** — installazione dipendenze e configurazione guidata. **`run_client.sh`** — attiva il venv e avvia il client. **`.env`** — URL server e API key.

```
cipher-client/
├── cipher_client.py
├── install.sh
├── run_client.sh
├── .env
└── README.md
```
