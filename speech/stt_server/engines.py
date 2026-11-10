"""Local STT engines. Each takes 16 kHz mono float audio and returns Roman Hinglish text."""

import json
import logging
import os
from typing import Optional

import numpy as np

from config.settings import get_settings
from speech.stt_server.mel import log_mel
from speech.transliterate import to_roman

logger = logging.getLogger(__name__)


class EngineUnavailable(RuntimeError):
    """The engine cannot load here. The message says what the user needs to do."""


class Engine:
    name = ""

    def load(self) -> None:
        raise NotImplementedError

    def transcribe(self, audio: np.ndarray) -> str:
        raise NotImplementedError

    def close(self) -> None:
        pass


class WhisperHinglishEngine(Engine):
    """Oriserve Whisper-Hindi2Hinglish-Apex through faster-whisper (CTranslate2, int8, CPU). Outputs Roman text."""

    name = "whisper_hinglish"

    def __init__(self):
        self.model = None

    def load(self) -> None:
        s = get_settings()
        if not os.path.isdir(s.STT_WHISPER_MODEL_PATH):
            raise EngineUnavailable(f"No converted model at {s.STT_WHISPER_MODEL_PATH}. "
                                    "See 'Local demo' in README.md for the conversion step.")
        from faster_whisper import WhisperModel

        self.model = WhisperModel(s.STT_WHISPER_MODEL_PATH, device=s.STT_WHISPER_DEVICE,
                                  compute_type=s.STT_WHISPER_COMPUTE_TYPE, cpu_threads=s.STT_WHISPER_THREADS)

    def transcribe(self, audio: np.ndarray) -> str:
        # The model card uses language "en": this model writes Hindi speech in Roman script under that setting
        segments, _ = self.model.transcribe(audio, language="en", task="transcribe", beam_size=1,
                                            condition_on_previous_text=False, vad_filter=False)
        return " ".join(segment.text.strip() for segment in segments).strip()

    def close(self) -> None:
        self.model = None


def onnx_providers(device: str, available: list) -> list:
    """cuda asks for the GPU; it needs onnxruntime-gpu installed, so without it the CPU is used and that is logged."""
    if device.lower().startswith("cuda"):
        if "CUDAExecutionProvider" in available:
            return ["CUDAExecutionProvider", "CPUExecutionProvider"]
        logger.warning("STT_INDICCONFORMER_DEVICE=cuda but onnxruntime-gpu is not installed; using the CPU")
    return ["CPUExecutionProvider"]


class IndicConformerEngine(Engine):
    """AI4Bharat IndicConformer 600M with CTC decoding, from the official gated ONNX files on onnxruntime (no torch).
    Outputs Devanagari, which is converted to Roman here."""

    name = "indicconformer"
    language = "hi"
    blank_id = 256

    def __init__(self):
        self.encoder = None
        self.decoder = None
        self.vocab = []
        self.mask = []

    def _ensure_files(self) -> str:
        s = get_settings()
        assets = os.path.join(s.STT_INDICCONFORMER_PATH, "assets")
        if os.path.exists(os.path.join(assets, "encoder.onnx")):
            return assets
        if not s.HF_TOKEN:
            raise EngineUnavailable("HF_TOKEN is not set. Accept the terms at "
                                    f"https://huggingface.co/{s.STT_INDICCONFORMER_MODEL}, then add a token to .env.")
        from huggingface_hub import snapshot_download

        snapshot_download(s.STT_INDICCONFORMER_MODEL, token=s.HF_TOKEN, local_dir=s.STT_INDICCONFORMER_PATH,
                          allow_patterns=["assets/*"], ignore_patterns=["assets/joint_*", "assets/rnnt_decoder.onnx"])
        return assets

    def load(self) -> None:
        import onnxruntime as ort

        assets = self._ensure_files()
        options = ort.SessionOptions()
        options.intra_op_num_threads = get_settings().STT_WHISPER_THREADS
        providers = onnx_providers(get_settings().STT_INDICCONFORMER_DEVICE, ort.get_available_providers())
        self.encoder = ort.InferenceSession(os.path.join(assets, "encoder.onnx"), options, providers=providers)
        self.decoder = ort.InferenceSession(os.path.join(assets, "ctc_decoder.onnx"), options, providers=providers)
        logger.info(f"IndicConformer runs on {self.encoder.get_providers()[0]}")
        with open(os.path.join(assets, "vocab.json"), encoding="utf-8") as f:
            self.vocab = json.load(f)[self.language]
        with open(os.path.join(assets, "language_masks.json"), encoding="utf-8") as f:
            self.mask = json.load(f)[self.language]

    def transcribe_devanagari(self, audio: np.ndarray) -> str:
        features, frames = log_mel(audio)
        outputs, _ = self.encoder.run(["outputs", "encoded_lengths"],
                                      {"audio_signal": features, "length": np.array([frames], dtype=np.int64)})
        logprobs = self.decoder.run(["logprobs"], {"encoder_output": outputs})[0]
        best = np.argmax(logprobs[0][:, self.mask], axis=-1)
        collapsed = best[np.insert(best[1:] != best[:-1], 0, True)]
        return "".join(self.vocab[int(i)] for i in collapsed if i != self.blank_id).replace("\u2581", " ").strip()

    def transcribe(self, audio: np.ndarray) -> str:
        return to_roman(self.transcribe_devanagari(audio))

    def close(self) -> None:
        self.encoder = None
        self.decoder = None


ENGINES = {WhisperHinglishEngine.name: WhisperHinglishEngine, IndicConformerEngine.name: IndicConformerEngine}


def create_engine(name: str) -> Optional[Engine]:
    if name not in ENGINES:
        raise EngineUnavailable(f"Unknown STT engine '{name}'. Choose from: {', '.join(ENGINES)}")
    return ENGINES[name]()
