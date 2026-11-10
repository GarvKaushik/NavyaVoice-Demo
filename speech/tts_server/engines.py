"""Voices the TTS service can use. Each takes one normalized Roman-Hinglish sentence and yields audio as it is made.

    maya   Maya 2 Native on an NVIDIA GPU (Linux), streamed in small chunks
    svara  Svara through llama.cpp, the whole sentence at once, with the agreed recipe:
           Devanagari, "।" for full stops, a <happy> tag, and 1.1x speed at the same pitch
    piper  Piper on the CPU, for trying the service on a laptop
"""

import logging
import os
import threading
from typing import Iterator, List

from config.settings import get_settings
from speech.devanagari import to_devanagari
from speech.tts_server.audio import Chunk, speed_up

logger = logging.getLogger(__name__)


class EngineUnavailable(RuntimeError):
    """The voice cannot load here. The message says what is missing."""


class TTSEngine:
    name = ""
    sample_rate = 24000
    streams = 1  # sentences it can make at once

    def load(self) -> None:
        raise NotImplementedError

    def stream(self, text: str, stop: threading.Event) -> Iterator[Chunk]:
        """Runs in a worker thread. Stops early once stop is set."""
        raise NotImplementedError

    def close(self) -> None:
        pass


class MayaEngine(TTSEngine):
    name = "maya"
    sample_rate = 24000

    def __init__(self):
        self.tts = None

    def load(self) -> None:
        try:
            from maya3 import Maya3TTS
        except ImportError as e:
            raise EngineUnavailable("Maya needs Linux, an NVIDIA GPU and the maya3 package "
                                    "(pip install -r requirements-tts-server.txt)") from e
        s = get_settings()
        self.streams = max(1, s.TTS_MAYA_SEATS)
        self.tts = Maya3TTS.from_pretrained(s.TTS_MAYA_MODEL, max_seats=self.streams)

    def stream(self, text: str, stop: threading.Event) -> Iterator[Chunk]:
        s = get_settings()
        spoken = to_devanagari(text) if s.TTS_MAYA_DEVANAGARI else text
        chunks = self.tts.stream(spoken, lang=s.TTS_MAYA_LANGUAGE, voice=s.TTS_MAYA_VOICE, normalize=True)
        try:
            for chunk in chunks:
                if stop.is_set():
                    break
                yield chunk
        finally:
            close = getattr(chunks, "close", None)
            if close:
                close()

    def close(self) -> None:
        if self.tts is not None:
            self.tts.close()
            self.tts = None


def svara_text(text: str) -> str:
    """The recipe that sounded best in the listening tests."""
    return f"{to_devanagari(text).replace('.', '।').strip()} <happy>"


class SvaraEngine(TTSEngine):
    name = "svara"
    max_new_tokens = 2048

    def __init__(self):
        self.llm = None
        self.snac = None
        self.torch = None
        self.device = "cpu"

    def _model_file(self) -> str:
        s = get_settings()
        path = s.TTS_SVARA_GGUF
        if os.path.exists(path):
            return path
        if not s.TTS_SVARA_GGUF_REPO:
            raise EngineUnavailable(f"Svara model file {path} is missing. Copy it there, or set TTS_SVARA_GGUF_REPO "
                                    "to the Hugging Face repo that holds it.")
        from huggingface_hub import hf_hub_download

        return hf_hub_download(s.TTS_SVARA_GGUF_REPO, os.path.basename(path), local_dir=os.path.dirname(path) or ".",
                               token=s.HF_TOKEN or None)

    def load(self) -> None:
        try:
            import torch
            from llama_cpp import Llama
            from snac import SNAC
        except ImportError as e:
            raise EngineUnavailable("Svara needs llama-cpp-python, snac and torch "
                                    "(pip install -r requirements-tts-server.txt)") from e
        from speech.local_tts_render import SAMPLE_RATE

        s = get_settings()
        self.sample_rate = SAMPLE_RATE
        self.llm = Llama(model_path=self._model_file(), n_ctx=4096, n_gpu_layers=s.TTS_SVARA_GPU_LAYERS,
                         n_threads=s.TTS_SVARA_THREADS, verbose=False)
        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.snac = SNAC.from_pretrained("hubertsiuzdak/snac_24khz").eval().to(self.device)

    def _tokens(self, text: str, stop: threading.Event) -> List[int]:
        from speech.local_tts_render import END_OF_AI, END_OF_SPEECH, SVARA_SPEAKER, svara_prompt

        prompt = svara_prompt(self.llm.tokenize(f"{SVARA_SPEAKER}: {text}".encode(), add_bos=False, special=False))
        out: List[int] = []
        for token in self.llm.generate(prompt, temp=0.75, top_p=0.9, repeat_penalty=1.1, reset=True):
            if token in (END_OF_SPEECH, END_OF_AI) or len(out) >= self.max_new_tokens or stop.is_set():
                break
            out.append(token)
        return out

    def stream(self, text: str, stop: threading.Event) -> Iterator[Chunk]:
        from speech.local_tts_render import split_frames, tokens_to_codes

        tokens = self._tokens(svara_text(text), stop)
        levels = split_frames(tokens_to_codes(tokens))
        if stop.is_set() or not levels[0]:
            return
        torch = self.torch
        with torch.no_grad():
            audio = self.snac.decode([torch.tensor([level], dtype=torch.int32, device=self.device) for level in levels])
        audio = audio.squeeze().clamp(-1, 1).float().cpu().numpy()
        yield speed_up(audio, self.sample_rate, get_settings().TTS_SVARA_SPEED)


class PiperEngine(TTSEngine):
    name = "piper"

    def __init__(self):
        self.voice = None

    def load(self) -> None:
        try:
            from speech.piper_tts import load_voice
        except ImportError as e:
            raise EngineUnavailable("Piper needs piper-tts (pip install -r requirements-local.txt)") from e
        self.voice = load_voice()
        self.sample_rate = self.voice.config.sample_rate

    def stream(self, text: str, stop: threading.Event) -> Iterator[Chunk]:
        from piper import SynthesisConfig

        config = SynthesisConfig(length_scale=get_settings().PIPER_LENGTH_SCALE)
        for chunk in self.voice.synthesize(to_devanagari(text), syn_config=config):
            if stop.is_set():
                break
            yield chunk.audio_int16_bytes


ENGINES = {MayaEngine.name: MayaEngine, SvaraEngine.name: SvaraEngine, PiperEngine.name: PiperEngine}


def create_engine(name: str) -> TTSEngine:
    if name not in ENGINES:
        raise EngineUnavailable(f"Unknown TTS engine {name!r}")
    return ENGINES[name]()
