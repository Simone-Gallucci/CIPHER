"""
modules/listener.py – Wake word detection e Speech-to-Text
"""

import json
import os
import queue
import sys
import time
from pathlib import Path
from typing import Optional

import sounddevice as sd
from vosk import KaldiRecognizer, Model
from rich.console import Console

from config import Config

console = Console()


class Listener:
    def __init__(self) -> None:
        old_fd2    = os.dup(2)
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull_fd, 2)
        os.close(devnull_fd)

        try:
            if not Path(Config.VOSK_MODEL_PATH).exists():
                raise FileNotFoundError(f"Modello Vosk IT non trovato: {Config.VOSK_MODEL_PATH}")
            self._model_it = Model(Config.VOSK_MODEL_PATH)

            self._model_en = None
            en_path = Path(Config.VOSK_WAKE_MODEL_PATH)
            if en_path.exists():
                self._model_en = Model(str(en_path))

            EN_WORDS = {"cipher", "jarvis"}
            self._wake_words_it = [w for w in Config.WAKE_WORDS if w not in EN_WORDS]
            self._wake_words_en = [w for w in Config.WAKE_WORDS if w in EN_WORDS]

            it_grammar = json.dumps(self._wake_words_it + ["[unk]"])
            self._wake_rec_it = KaldiRecognizer(self._model_it, Config.SAMPLE_RATE, it_grammar)

            self._wake_rec_en = None
            if self._model_en and self._wake_words_en:
                en_grammar = json.dumps(self._wake_words_en + ["[unk]"])
                self._wake_rec_en = KaldiRecognizer(self._model_en, Config.SAMPLE_RATE, en_grammar)

            self._cmd_rec = KaldiRecognizer(self._model_it, Config.SAMPLE_RATE)
            self._audio_queue: queue.Queue = queue.Queue()
            self._stream: Optional[sd.RawInputStream] = None

        finally:
            os.dup2(old_fd2, 2)
            os.close(old_fd2)

        active = self._wake_words_it[:]
        if self._wake_rec_en:
            active += self._wake_words_en
        console.print("[green]✓ Listener pronto[/green]")
        console.print(f"[green]✓  Wake words: {', '.join(active)}[/green]")

    def _audio_callback(self, indata, frames, time_info, status) -> None:
        if status:
            console.print(f"[yellow]⚠ Audio: {status}[/yellow]")
        self._audio_queue.put(bytes(indata))

    def start(self) -> None:
        device = None if Config.MIC_DEVICE_INDEX == -1 else Config.MIC_DEVICE_INDEX
        self._stream = sd.RawInputStream(
            samplerate=Config.SAMPLE_RATE,
            blocksize=Config.BLOCK_SIZE,
            device=device,
            dtype="int16",
            channels=Config.CHANNELS,
            callback=self._audio_callback,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def wait_for_wake_word(self) -> None:
        while not self._audio_queue.empty():
            self._audio_queue.get_nowait()
        while True:
            data = self._audio_queue.get()
            if self._wake_rec_it.AcceptWaveform(data):
                text = json.loads(self._wake_rec_it.Result()).get("text", "").lower()
            else:
                text = json.loads(self._wake_rec_it.PartialResult()).get("partial", "").lower()
            if any(w in text for w in self._wake_words_it):
                return
            if self._wake_rec_en:
                if self._wake_rec_en.AcceptWaveform(data):
                    text_en = json.loads(self._wake_rec_en.Result()).get("text", "").lower()
                else:
                    text_en = json.loads(self._wake_rec_en.PartialResult()).get("partial", "").lower()
                if any(w in text_en for w in self._wake_words_en):
                    return

    def listen_command(self, voice=None) -> str:
        if voice:
            while voice.is_speaking:
                time.sleep(0.1)
        time.sleep(1.2)
        self._cmd_rec = KaldiRecognizer(self._model_it, Config.SAMPLE_RATE)
        while not self._audio_queue.empty():
            self._audio_queue.get_nowait()

        parts       = []
        last_speech = time.time()
        got_speech  = False
        start_time  = time.time()

        while True:
            try:
                data = self._audio_queue.get(timeout=0.5)
            except queue.Empty:
                if got_speech and (time.time() - last_speech) > Config.SILENCE_TIMEOUT:
                    break
                if not got_speech and (time.time() - start_time) > 6.0:
                    break
                continue
            if self._cmd_rec.AcceptWaveform(data):
                text = json.loads(self._cmd_rec.Result()).get("text", "").strip()
                if text:
                    parts.append(text)
                    last_speech = time.time()
                    got_speech  = True
            else:
                partial = json.loads(self._cmd_rec.PartialResult()).get("partial", "").strip()
                if partial:
                    last_speech = time.time()
                    got_speech  = True
            if got_speech and (time.time() - last_speech) > Config.SILENCE_TIMEOUT:
                break

        final = json.loads(self._cmd_rec.FinalResult()).get("text", "").strip()
        if final:
            parts.append(final)
        result = " ".join(parts).strip()
        all_wake = self._wake_words_it + self._wake_words_en
        if result.lower() in all_wake:
            return ""
        if result:
            console.print(f"[cyan]📝 Voce:[/cyan] [bold]{result}[/bold]")
        return result

    def transcribe_audio(self, audio_bytes: bytes) -> str:
        """Trascrive audio grezzo PCM 16bit 16000Hz mono."""
        rec = KaldiRecognizer(self._model_it, Config.SAMPLE_RATE)
        chunk_size = 4000 * 2
        parts = []
        for i in range(0, len(audio_bytes), chunk_size):
            chunk = audio_bytes[i:i + chunk_size]
            if rec.AcceptWaveform(chunk):
                text = json.loads(rec.Result()).get("text", "").strip()
                if text:
                    parts.append(text)
        final = json.loads(rec.FinalResult()).get("text", "").strip()
        if final:
            parts.append(final)
        return " ".join(parts).strip()

    def check_wake_word(self, audio_bytes: bytes) -> bool:
        """Controlla se l'audio contiene una wake word."""
        rec_it = KaldiRecognizer(
            self._model_it, Config.SAMPLE_RATE,
            json.dumps(self._wake_words_it + ["[unk]"])
        )
        chunk_size = 4000 * 2
        for i in range(0, len(audio_bytes), chunk_size):
            chunk = audio_bytes[i:i + chunk_size]
            if rec_it.AcceptWaveform(chunk):
                text = json.loads(rec_it.Result()).get("text", "").lower()
            else:
                text = json.loads(rec_it.PartialResult()).get("partial", "").lower()
            if any(w in text for w in self._wake_words_it):
                return True
            if self._wake_rec_en:
                rec_en = KaldiRecognizer(
                    self._model_en, Config.SAMPLE_RATE,
                    json.dumps(self._wake_words_en + ["[unk]"])
                )
                if rec_en.AcceptWaveform(chunk):
                    text_en = json.loads(rec_en.Result()).get("text", "").lower()
                else:
                    text_en = json.loads(rec_en.PartialResult()).get("partial", "").lower()
                if any(w in text_en for w in self._wake_words_en):
                    return True
        return False
