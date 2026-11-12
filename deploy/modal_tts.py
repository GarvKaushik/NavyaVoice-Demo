"""TTS service on Modal: Maya 2 Native, with Svara as the fallback voice. Only used to deploy.

    pip install modal && modal setup                                   # once, on the machine you deploy from
    modal secret create navya-speech SPEECH_SERVER_TOKEN=<secret> HF_TOKEN=<token>
    modal volume put navya-models models/svara/svara-tts-v1-Q3_K_M.gguf svara/svara-tts-v1-Q3_K_M.gguf
    modal deploy deploy/modal_tts.py                                   # T4 (default)
    NAVYA_TTS_GPU=L4 modal deploy deploy/modal_tts.py                  # another GPU
    NAVYA_MIN_CONTAINERS=1 modal deploy deploy/modal_tts.py            # keep one warm during a test session

Modal prints a link like https://<workspace>--navya-tts-web.modal.run. In the pipeline's .env:
    TTS_PROVIDER=server
    TTS_SERVER_URL=wss://<workspace>--navya-tts-web.modal.run/v1/tts
    SPEECH_SERVER_TOKEN=<the same secret>

Maya's weights download into the navya-models volume on the first start, so that start takes minutes.
"""

import os
import pathlib

import modal

ROOT = pathlib.Path(__file__).resolve().parent.parent
GPU = os.environ.get("NAVYA_TTS_GPU", "T4")  # Maya needs about 8-10 GB for one seat; a T4 has 16 GB
MIN_CONTAINERS = int(os.environ.get("NAVYA_MIN_CONTAINERS", "0"))

image = (
    modal.Image.from_registry("nvidia/cuda:12.8.1-devel-ubuntu22.04", add_python="3.11")
    .apt_install("git", "build-essential", "cmake")
    # The base image leaves CC empty, which stops llama.cpp's build. LIBRARY_PATH lets it link against the CUDA
    # driver's stub on a build machine without a GPU; at run time the real driver is used.
    .env({"CC": "gcc", "CXX": "g++", "LIBRARY_PATH": "/usr/local/cuda/lib64/stubs"})
    .pip_install_from_requirements(str(ROOT / "requirements.txt"))
    .pip_install_from_requirements(str(ROOT / "requirements-local.txt"))
    # Svara's model runs on the GPU only with a CUDA build of llama.cpp. The build machine has no GPU, so the
    # GPU generations are named: T4 (75), A100 (80), A10G (86), L4 (89).
    .run_commands('CMAKE_ARGS="-DGGML_CUDA=on -DCMAKE_CUDA_ARCHITECTURES=75;80;86;89" '
                  'pip install --no-cache-dir "llama-cpp-python>=0.3.0"')
    .pip_install_from_requirements(str(ROOT / "requirements-tts-server.txt"),
                                   extra_index_url="https://download.pytorch.org/whl/cu128")
    .env({
        "TTS_ENGINE": "maya",
        "TTS_FALLBACK_ENGINE": "svara",
        "TTS_SVARA_GGUF": "/models/svara/svara-tts-v1-Q3_K_M.gguf",
        "TTS_SVARA_GPU_LAYERS": "-1",
        "HF_HOME": "/models/hf",
    })
    .add_local_python_source("config", "speech", "observability")
)

app = modal.App("navya-tts", image=image)
models = modal.Volume.from_name("navya-models", create_if_missing=True)


@app.function(
    gpu=GPU,
    cpu=4.0,
    memory=16384,
    volumes={"/models": models},
    secrets=[modal.Secret.from_name("navya-speech")],
    min_containers=MIN_CONTAINERS,
    scaledown_window=300,  # an idle container stops after 5 minutes, so credits aren't spent between tests
    timeout=3600,  # one WebSocket lasts a whole call
)
@modal.concurrent(max_inputs=50)
@modal.asgi_app()
def web():
    from speech.tts_server.app import app as service

    return service


# ------------------------------------------------------------------------------
# Listening test: the same sales lines in several Maya voices, Roman vs Devanagari script.
#   python -m modal run deploy/modal_tts.py::samples
#   python -m modal run deploy/modal_tts.py::samples --voices "Nivedita,Jahnavi"
#   python -m modal run deploy/modal_tts.py::samples --engine svara      # the fallback voice
# Writes WAV files to assets/maya_voices (or assets/svara_voices), which git ignores; Maya clips at normal
# speed and at 1.1x (same pitch). Doesn't touch the service.
# ------------------------------------------------------------------------------
SAMPLE_LINES = [
    "Namaste Rajesh ji! Main Priya bol rahi hoon, NavyaNexus se. Kya abhi do minute baat ho sakti hai?",
    "Yeh plan sirf 1,499 rupaye mahine ka hai, aur pehle saat din bilkul free hain.",
    "Bilkul samajh sakti hoon. Kya main aapko WhatsApp par details bhej doon?",
]
SAMPLE_VOICES = "Ahana,Nivedita,Tanushree,Jahnavi,Ishwari"


@app.function(gpu=GPU, volumes={"/models": models}, secrets=[modal.Secret.from_name("navya-speech")], timeout=1800)
def svara_samples(lines: list) -> dict:
    """Svara exactly as the service uses it: Devanagari, "।" for full stops, a <happy> tag, 1.1x speed."""
    import threading

    import numpy as np

    from speech.normalizer import HinglishNormalizer
    from speech.tts_server.audio import to_float
    from speech.tts_server.engines import SvaraEngine

    engine = SvaraEngine()
    engine.load()
    clips = {}
    for number, line in enumerate(lines, 1):
        pieces = [to_float(c) for c in engine.stream(HinglishNormalizer.normalize(line), threading.Event())]
        clips[f"Svara_line{number}"] = np.concatenate(pieces).astype(np.float32).tobytes() if pieces else b""
    return clips


@app.function(gpu=GPU, volumes={"/models": models}, secrets=[modal.Secret.from_name("navya-speech")], timeout=1800)
def voice_samples(voices: list, lines: list) -> dict:
    import numpy as np
    from maya3 import Maya3TTS

    from config.settings import get_settings
    from speech.devanagari import to_devanagari
    from speech.normalizer import HinglishNormalizer

    tts = Maya3TTS.from_pretrained(get_settings().TTS_MAYA_MODEL, max_seats=1)
    clips = {}
    try:
        for voice in voices:
            for number, line in enumerate(lines, 1):
                spoken = HinglishNormalizer.normalize(line)
                for script, text in (("roman", spoken), ("devanagari", to_devanagari(spoken))):
                    audio = tts.generate(text, lang="hi", voice=voice, normalize=True)
                    clips[f"{voice}_line{number}_{script}"] = np.asarray(audio, dtype=np.float32).tobytes()
    finally:
        tts.close()
    return clips


@app.local_entrypoint()
def samples(voices: str = SAMPLE_VOICES, out: str = "", engine: str = "maya"):
    """--engine svara renders the fallback voice instead (already at its 1.1x recipe speed)."""
    import wave

    import numpy as np

    from speech.stt_server.audio import float_to_pcm16
    from speech.tts_server.audio import speed_up

    folder = pathlib.Path(out) if out else ROOT / "assets" / f"{engine}_voices"  # ignored by git
    folder.mkdir(parents=True, exist_ok=True)
    if engine == "svara":
        clips, speeds = svara_samples.remote(SAMPLE_LINES), (("", 1.0),)
    else:
        clips = voice_samples.remote([v.strip() for v in voices.split(",") if v.strip()], SAMPLE_LINES)
        speeds = (("", 1.0), ("_fast", 1.1))
    for name, raw in clips.items():
        audio = np.frombuffer(raw, dtype=np.float32)
        for suffix, factor in speeds:
            take = speed_up(audio, 24000, factor)
            with wave.open(str(folder / f"{name}{suffix}.wav"), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(24000)
                w.writeframes(float_to_pcm16(take))
    print(f"{len(clips) * len(speeds)} files in {folder}")
