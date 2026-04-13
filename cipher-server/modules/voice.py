"""
modules/voice.py – TTS con ElevenLabs (George, multilingua)
"""

import os
import re
import subprocess
import tempfile
import threading
import time
from typing import Optional

from rich.console import Console
from config import Config

console = Console()


def _get_mic_source() -> Optional[str]:
    try:
        r = subprocess.run(["pactl", "get-default-source"], capture_output=True, text=True)
        return r.stdout.strip() or None
    except Exception:
        return None


def _mute_mic(mute: bool) -> None:
    source = _get_mic_source()
    if not source:
        return
    try:
        subprocess.run(
            ["pactl", "set-source-mute", source, "1" if mute else "0"],
            capture_output=True
        )
    except Exception:
        pass


class Voice:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled   = enabled
        self._lock     = threading.Lock()
        self._speaking = False
        self._client   = None

        if self.enabled:
            try:
                from elevenlabs.client import ElevenLabs
                self._client = ElevenLabs(api_key=Config.ELEVENLABS_API_KEY)
                console.print("[green]✓ Voice (ElevenLabs - George) pronta[/green]")
            except ImportError:
                console.print("[yellow]⚠ elevenlabs non trovato — pip install elevenlabs[/yellow]")
                self.enabled = False
            except Exception as e:
                console.print(f"[yellow]⚠ ElevenLabs errore: {e}[/yellow]")
                self.enabled = False
        else:
            console.print("[dim]  Voice: disabilitata (--no-tts)[/dim]")

    def speak(self, text: str) -> None:
        if not text or not text.strip():
            return
        clean = self._clean(text)
        console.print(f"[bold magenta]Cipher:[/bold magenta] {clean}")
        if not self.enabled:
            return

        # Genera audio FUORI dal lock per non bloccare altri thread durante I/O di rete
        tmp_path = None
        try:
            audio = self._client.text_to_speech.convert(
                voice_id=Config.ELEVENLABS_VOICE_ID,
                text=clean,
                model_id="eleven_multilingual_v2",
            )
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                tmp_path = tmp.name
                for chunk in audio:
                    tmp.write(chunk)
        except Exception as e:
            console.print(f"[red]Errore TTS: {e}[/red]")
            return

        # Riproduzione sotto lock (breve: solo playback locale)
        with self._lock:
            self._speaking = True
            _mute_mic(True)
            try:
                subprocess.run(["mpg123", "-q", tmp_path], capture_output=True)
            except Exception as e:
                console.print(f"[red]Errore playback: {e}[/red]")
            finally:
                time.sleep(0.3)
                _mute_mic(False)
                self._speaking = False
                if tmp_path and os.path.exists(tmp_path):
                    os.unlink(tmp_path)

    def speak_async(self, text: str) -> threading.Thread:
        t = threading.Thread(target=self.speak, args=(text,), daemon=True)
        t.start()
        return t

    def synthesize(self, text: str) -> Optional[bytes]:
        """Genera audio TTS e restituisce i bytes MP3 senza riprodurli."""
        if not text or not text.strip() or not self.enabled:
            return None
        clean = self._clean(text)
        try:
            audio = self._client.text_to_speech.convert(
                voice_id=Config.ELEVENLABS_VOICE_ID,
                text=clean,
                model_id="eleven_multilingual_v2",
            )
            return b"".join(audio)
        except Exception as e:
            console.print(f"[red]Errore TTS synthesize: {e}[/red]")
            return None

    def stop(self) -> None:
        try:
            subprocess.run(["pkill", "-f", "mpg123"], capture_output=True)
        except Exception:
            pass
        _mute_mic(False)
        self._speaking = False

    @property
    def is_speaking(self) -> bool:
        return self._speaking

    @staticmethod
    def _clean(text: str) -> str:
        text = re.sub(r'[*#`_~]',              '',     text)
        text = re.sub(r'https?://\S+',          'link', text)
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1',  text)
        text = re.sub(r'\s+',                   ' ',    text)
        return text.strip()
