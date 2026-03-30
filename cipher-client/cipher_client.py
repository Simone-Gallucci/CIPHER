"""
cipher_client.py – Client per Cipher Server
Connette al Raspberry Pi via Tailscale e usa microfono/audio locali.

Supporto microfono:
  - Termux (Android): termux-microphone-record + ffmpeg
  - Linux con PipeWire: pw-record + ffmpeg
  - Linux con sounddevice: fallback ALSA/PulseAudio
"""

import argparse
import os
import queue
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time

import requests
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()

DEFAULT_SERVER = os.getenv("CIPHER_SERVER_URL", "http://100.127.57.5:5000")
EXIT_TRIGGERS  = {"esci", "exit", "quit", "shutdown", "spegni"}
RESET_TRIGGERS = {"resetta", "reset", "nuova conversazione"}
STOP_TRIGGERS  = {"stop", "basta", "fine", "pausa"}

SAMPLE_RATE = 16000
BLOCK_SIZE  = 4000
CHANNELS    = 1


def is_exit(text: str) -> bool:
    return any(t in text.lower().strip() for t in EXIT_TRIGGERS)


def is_reset(text: str) -> bool:
    return any(t in text.lower().strip() for t in RESET_TRIGGERS)


def is_stop(text: str) -> bool:
    return any(t in text.lower().strip() for t in STOP_TRIGGERS)


def clean_text(text: str) -> str:
    text = re.sub(r'[*#`_~]',              '',     text)
    text = re.sub(r'https?://\S+',          'link', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1',  text)
    text = re.sub(r'\s+',                   ' ',    text)
    return text.strip()


# ── Server ────────────────────────────────────────────────────────────

def check_server(server_url: str) -> bool:
    try:
        r = requests.get(f"{server_url}/health", timeout=5)
        return r.status_code == 200
    except Exception:
        return False


def send_message(server_url: str, message: str) -> str:
    try:
        r = requests.post(
            f"{server_url}/chat",
            json={"message": message},
            timeout=30
        )
        return r.json().get("response", "Nessuna risposta.")
    except Exception as e:
        return f"Errore connessione: {e}"


def reset_session(server_url: str) -> None:
    try:
        requests.post(f"{server_url}/reset", timeout=5)
    except Exception:
        pass


def stt_remote(server_url: str, audio_bytes: bytes) -> str:
    try:
        r = requests.post(
            f"{server_url}/stt",
            data=audio_bytes,
            headers={"Content-Type": "application/octet-stream"},
            timeout=15
        )
        return r.json().get("text", "")
    except Exception:
        return ""


def check_wake_remote(server_url: str, audio_bytes: bytes) -> bool:
    try:
        r = requests.post(
            f"{server_url}/wake",
            data=audio_bytes,
            headers={"Content-Type": "application/octet-stream"},
            timeout=5
        )
        return r.json().get("detected", False)
    except Exception:
        return False


# ── TTS (ElevenLabs) ──────────────────────────────────────────────────

class Voice:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled   = enabled
        self._speaking = False
        self._lock     = threading.Lock()
        self._api_key  = os.getenv("ELEVENLABS_API_KEY", "")
        self._voice_id = os.getenv("ELEVENLABS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb")
        self._client   = None

        if self.enabled:
            if not self._api_key:
                console.print("[yellow]⚠ ELEVENLABS_API_KEY non impostata — TTS disabilitato[/yellow]")
                self.enabled = False
            else:
                try:
                    from elevenlabs.client import ElevenLabs
                    self._client = ElevenLabs(api_key=self._api_key)
                    console.print("[green]✓ Voice (ElevenLabs) pronta[/green]")
                except ImportError:
                    console.print("[yellow]⚠ elevenlabs non installato[/yellow]")
                    self.enabled = False
        else:
            console.print("[dim]  Voice: disabilitata[/dim]")

    def _mute_mic(self, mute: bool) -> None:
        try:
            source = subprocess.run(
                ["pactl", "get-default-source"], capture_output=True, text=True
            ).stdout.strip()
            if source:
                subprocess.run(
                    ["pactl", "set-source-mute", source, "1" if mute else "0"],
                    capture_output=True
                )
        except Exception:
            pass

    def speak(self, text: str) -> None:
        clean = clean_text(text)
        console.print(f"[bold magenta]Cipher:[/bold magenta] {clean}")
        if not self.enabled:
            return
        with self._lock:
            self._speaking = True
            self._mute_mic(True)
            tmp_path = None
            try:
                audio = self._client.text_to_speech.convert(
                    voice_id=self._voice_id,
                    text=clean,
                    model_id="eleven_multilingual_v2",
                )
                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                    tmp_path = tmp.name
                    for chunk in audio:
                        tmp.write(chunk)
                subprocess.run(["mpg123", "-q", tmp_path], capture_output=True)
            except Exception as e:
                console.print(f"[red]Errore TTS: {e}[/red]")
            finally:
                time.sleep(0.3)
                self._mute_mic(False)
                self._speaking = False
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)

    def speak_async(self, text: str) -> threading.Thread:
        t = threading.Thread(target=self.speak, args=(text,), daemon=True)
        t.start()
        return t

    def wait_until_done(self) -> None:
        while self._speaking:
            time.sleep(0.1)

    @property
    def is_speaking(self) -> bool:
        return self._speaking


# ── Microfono ─────────────────────────────────────────────────────────

class RemoteListener:
    def __init__(self, server_url: str) -> None:
        self._server_url  = server_url
        self._audio_buf   = []
        self._lock        = threading.Lock()
        self._stream      = None
        self._use_termux  = False
        self._use_pipewire = False
        self._sd          = None

        # ── Rileva il metodo di registrazione migliore ────────────────
        if os.path.exists("/data/data/com.termux"):
            # Android/Termux
            self._use_termux = True
            console.print("[green]✓ Microfono pronto (termux-mic)[/green]")

        elif subprocess.run(["which", "pw-record"], capture_output=True).returncode == 0:
            # Linux con PipeWire
            self._use_pipewire = True
            # Trova il target microfono
            self._pw_target = os.getenv("PW_MIC_TARGET", "")
            if not self._pw_target:
                # Cerca automaticamente il primo microfono disponibile
                result = subprocess.run(
                    ["pw-cli", "list-objects", "Node"],
                    capture_output=True, text=True
                )
                # Usa il default se non trovato
                self._pw_target = ""
            console.print("[green]✓ Microfono pronto (pipewire)[/green]")

        else:
            # Fallback sounddevice
            try:
                import sounddevice as sd
                sd.query_devices()
                self._sd = sd
                console.print("[green]✓ Microfono pronto (sounddevice)[/green]")
            except Exception:
                self._use_termux = True
                console.print("[yellow]⚠ Nessun microfono trovato — fallback termux-mic[/yellow]")

    def _callback(self, indata, frames, time_info, status) -> None:
        with self._lock:
            self._audio_buf.append(bytes(indata))

    def start(self) -> None:
        if self._use_termux or self._use_pipewire:
            return
        device_idx = int(os.getenv("MIC_DEVICE_INDEX", -1))
        device = None if device_idx == -1 else device_idx
        self._stream = self._sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=BLOCK_SIZE,
            dtype="int16",
            channels=CHANNELS,
            callback=self._callback,
            device=device,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _get_audio(self) -> bytes:
        with self._lock:
            data = b"".join(self._audio_buf)
            self._audio_buf.clear()
        return data

    def _record_termux(self, seconds: float) -> bytes:
        """Registra audio su Android con termux-microphone-record."""
        home     = os.path.expanduser("~")
        tmp_opus = os.path.join(home, "cipher_rec.opus")
        tmp_pcm  = os.path.join(home, "cipher_rec.pcm")

        subprocess.run(["termux-microphone-record", "-q"], capture_output=True)
        time.sleep(0.3)

        for f in [tmp_opus, tmp_pcm]:
            try:
                os.unlink(f)
            except Exception:
                pass

        proc = subprocess.Popen(
            ["termux-microphone-record", "-l", str(int(seconds)),
             "-f", tmp_opus, "-e", "opus", "-r", "48000"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        time.sleep(seconds + 1.0)
        proc.wait()

        subprocess.run(["termux-microphone-record", "-q"], capture_output=True)
        time.sleep(0.5)

        try:
            if os.path.getsize(tmp_opus) < 1000:
                return b""
        except Exception:
            return b""

        result = subprocess.run(
            ["ffmpeg", "-y", "-i", tmp_opus,
             "-ar", "16000", "-ac", "1", "-f", "s16le", tmp_pcm],
            capture_output=True
        )

        if result.returncode != 0:
            return b""

        try:
            with open(tmp_pcm, "rb") as f:
                return f.read()
        except Exception:
            return b""

    def _record_pipewire(self, seconds: float) -> bytes:
        """Registra audio su Linux con PipeWire (pw-record)."""
        tmp_wav = tempfile.mktemp(suffix=".wav")
        tmp_pcm = tempfile.mktemp(suffix=".pcm")

        try:
            cmd = ["pw-record", "--rate=48000", "--channels=1",
                   "--format=s16", f"--target={self._pw_target}" if self._pw_target else "",
                   tmp_wav]
            cmd = [c for c in cmd if c]  # rimuovi stringhe vuote

            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(seconds)
            proc.terminate()
            proc.wait(timeout=2)
            time.sleep(0.3)

            if not os.path.exists(tmp_wav) or os.path.getsize(tmp_wav) < 1000:
                return b""

            result = subprocess.run(
                ["ffmpeg", "-y", "-i", tmp_wav,
                 "-ar", "16000", "-ac", "1", "-f", "s16le", tmp_pcm],
                capture_output=True
            )

            if result.returncode != 0:
                return b""

            with open(tmp_pcm, "rb") as f:
                return f.read()

        except Exception as e:
            console.print(f"[red]Errore PipeWire: {e}[/red]")
            return b""
        finally:
            for f in [tmp_wav, tmp_pcm]:
                try:
                    os.unlink(f)
                except Exception:
                    pass

    def _record(self, seconds: float) -> bytes:
        """Seleziona automaticamente il metodo di registrazione."""
        if self._use_termux:
            return self._record_termux(seconds)
        elif self._use_pipewire:
            return self._record_pipewire(seconds)
        else:
            # sounddevice — registra per N secondi e ritorna
            with self._lock:
                self._audio_buf.clear()
            time.sleep(seconds)
            return self._get_audio()

    def wait_for_wake_word(self, voice=None) -> None:
        console.print("[dim]👂 In ascolto...[/dim]")

        if self._use_termux or self._use_pipewire:
            while True:
                if voice:
                    voice.wait_until_done()
                    time.sleep(1.0)
                audio = self._record(2.0)
                if not audio:
                    continue
                heard = stt_remote(self._server_url, audio)
                if heard:
                    console.print(f"[dim]🎤 '{heard}'[/dim]")
                if check_wake_remote(self._server_url, audio):
                    return
        else:
            # sounddevice — stream continuo
            with self._lock:
                self._audio_buf.clear()
            buf = b""
            while True:
                time.sleep(0.5)
                chunk = self._get_audio()
                if not chunk:
                    continue
                buf += chunk
                if len(buf) >= SAMPLE_RATE * 2:
                    heard = stt_remote(self._server_url, buf)
                    if heard:
                        console.print(f"[dim]🎤 '{heard}'[/dim]")
                    if check_wake_remote(self._server_url, buf):
                        return
                    buf = buf[-(SAMPLE_RATE):]

    def listen_command(self, voice=None) -> str:
        if voice:
            voice.wait_until_done()
        time.sleep(4.0)

        console.print("[dim]🎙 Parla...[/dim]")
        audio = self._record(4.0)

        if not audio:
            return ""

        console.print("[dim]→ Trascrivo...[/dim]")
        result = stt_remote(self._server_url, audio)

        wake_words = [w.strip().lower() for w in os.getenv("WAKE_WORDS", "cipher,jarvis,ehi").split(",")]
        if result.lower().strip() in wake_words:
            return ""

        if result:
            console.print(f"[cyan]📝 Voce:[/cyan] [bold]{result}[/bold]")
        return result


# ── Modalità ──────────────────────────────────────────────────────────

def run_text(server_url: str, voice: Voice) -> None:
    console.print("\n[bold green]✓ Modalità TESTO[/bold green]\n")
    while True:
        try:
            console.print("[bold cyan]IO:[/bold cyan] ", end="")
            user_input = input().strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user_input:
            continue
        if is_exit(user_input):
            voice.speak("Arrivederci!")
            sys.exit(0)
        if is_reset(user_input):
            reset_session(server_url)
            voice.speak("Sessione resettata.")
            continue
        response = send_message(server_url, user_input)
        voice.speak(response)


def run_full(server_url: str, voice: Voice) -> None:
    listener = RemoteListener(server_url)
    listener.start()

    cmd_queue  = queue.Queue()
    stop_event = threading.Event()

    def voice_thread():
        try:
            while not stop_event.is_set():
                listener.wait_for_wake_word(voice=voice)
                if stop_event.is_set():
                    break
                cmd_queue.put(("_wake", ""))

                # Conversazione continua fino a "stop/basta/fine"
                while not stop_event.is_set():
                    command = listener.listen_command(voice=voice)
                    if not command:
                        time.sleep(1.0)
                        continue
                    if is_exit(command):
                        cmd_queue.put(("voice", command))
                        return
                    if is_stop(command):
                        console.print("[dim]💤 Torno in ascolto...[/dim]")
                        break
                    cmd_queue.put(("voice", command))

        except Exception as e:
            console.print(f"[red]Errore thread voce: {e}[/red]")
        finally:
            listener.stop()

    def text_thread():
        while not stop_event.is_set():
            try:
                text = input().strip()
                if text:
                    cmd_queue.put(("text", text))
            except (EOFError, KeyboardInterrupt):
                cmd_queue.put(("text", "esci"))
                break

    threading.Thread(target=voice_thread, daemon=True).start()
    threading.Thread(target=text_thread,  daemon=True).start()

    time.sleep(1.0)
    console.print("\n[bold green]✓ Modalità FULL[/bold green]\n")

    processing_lock = threading.Lock()

    try:
        while True:
            try:
                source, text = cmd_queue.get(timeout=0.5)
            except queue.Empty:
                continue

            if source == "_wake":
                if not processing_lock.locked() and cmd_queue.empty():
                    voice.speak("Eccomi, dimmi pure.")
                continue

            if not text:
                continue
            if is_exit(text):
                stop_event.set()
                voice.speak("Arrivederci!")
                sys.exit(0)
            if is_reset(text):
                reset_session(server_url)
                voice.speak("Sessione resettata.")
                continue

            if processing_lock.acquire(blocking=False):
                try:
                    response = send_message(server_url, text)
                    voice.speak(response)
                finally:
                    processing_lock.release()
            else:
                cmd_queue.put((source, text))
    finally:
        stop_event.set()


# ── Main ──────────────────────────────────────────────────────────────

def main() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(override=True)
    except ImportError:
        pass

    parser = argparse.ArgumentParser(description="Cipher Client")
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--no-tts", action="store_true")
    args = parser.parse_args()

    t = Text()
    t.append("  C I P H E R  –  Client\n", style="bold white on blue")
    t.append(f"  Server: {args.server}\n",  style="dim cyan")
    console.print(Panel(t, expand=False, border_style="blue"))

    console.print(f"[cyan]→ Connessione a {args.server}...[/cyan]")
    if not check_server(args.server):
        console.print(f"[red]✗ Server non raggiungibile: {args.server}[/red]")
        console.print("[yellow]  → Verifica che il Raspberry sia acceso e il server attivo[/yellow]")
        sys.exit(1)
    console.print("[green]✓ Connesso al server[/green]")

    print()
    console.print("  [cyan]1[/cyan]) Full mode     — Testo + Microfono + Voce")
    console.print("  [cyan]2[/cyan]) Testo + Voce  — Tastiera + risposta vocale")
    console.print("  [cyan]3[/cyan]) Solo testo    — Nessun audio")
    print()
    scelta = input("  → Scelta [1/2/3]: ").strip()

    voice = Voice(enabled=not args.no_tts and scelta in ("1", "2"))

    def shutdown(sig=None, frame=None):
        console.print("\n[yellow]Disconnessione...[/yellow]")
        voice.speak("Arrivederci!")
        sys.exit(0)

    signal.signal(signal.SIGINT,  shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    if scelta == "1":
        run_full(args.server, voice)
    elif scelta == "2":
        run_text(args.server, voice)
    elif scelta == "3":
        run_text(args.server, Voice(enabled=False))
    else:
        console.print("[red]Scelta non valida[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
