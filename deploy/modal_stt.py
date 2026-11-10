"""STT service on Modal. Only used to deploy; the pipeline never imports it.

    pip install modal && modal setup                                   # once, on the machine you deploy from
    modal secret create navya-speech SPEECH_SERVER_TOKEN=<secret> HF_TOKEN=<token>
    modal deploy deploy/modal_stt.py                                   # CPU (default)
    NAVYA_STT_GPU=T4 modal deploy deploy/modal_stt.py                  # GPU instead
    NAVYA_MIN_CONTAINERS=1 modal deploy deploy/modal_stt.py            # keep one warm during a test session

Modal prints a link like https://<workspace>--navya-stt-web.modal.run. In the pipeline's .env:
    STT_SERVER_URL=wss://<workspace>--navya-stt-web.modal.run/v1/stt
    SPEECH_SERVER_TOKEN=<the same secret>

IndicConformer is gated: accept its terms on Hugging Face first. The model downloads into the navya-models
volume on the first call, so that call is slow; later containers start from the volume.
"""

import os
import pathlib

import modal

ROOT = pathlib.Path(__file__).resolve().parent.parent
GPU = os.environ.get("NAVYA_STT_GPU", "")
MIN_CONTAINERS = int(os.environ.get("NAVYA_MIN_CONTAINERS", "0"))

env = {
    "STT_PROVIDER": "indicconformer",
    "STT_INDICCONFORMER_PATH": "/models/indicconformer",
    "STT_INDICCONFORMER_DEVICE": "cuda" if GPU else "cpu",
    "STT_WHISPER_MODEL_PATH": "/models/whisper-hinglish-apex-ct2",
    "STT_WHISPER_DEVICE": "cuda" if GPU else "cpu",
    "HF_HOME": "/models/hf",
}

if GPU:
    # onnxruntime-gpu needs the CUDA 12 and cuDNN 9 libraries, which this base image carries
    image = (modal.Image.from_registry("nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04", add_python="3.11")
             .pip_install_from_requirements(str(ROOT / "requirements.txt"))
             .pip_install_from_requirements(str(ROOT / "requirements-local.txt"))
             .run_commands("pip uninstall -y onnxruntime", "pip install 'onnxruntime-gpu>=1.20.0'"))
else:
    image = (modal.Image.debian_slim(python_version="3.11")
             .pip_install_from_requirements(str(ROOT / "requirements.txt"))
             .pip_install_from_requirements(str(ROOT / "requirements-local.txt")))
image = image.env(env).add_local_python_source("config", "speech", "observability")

app = modal.App("navya-stt", image=image)
models = modal.Volume.from_name("navya-models", create_if_missing=True)


@app.function(
    gpu=GPU or None,
    cpu=4.0,
    memory=8192,
    volumes={"/models": models},
    secrets=[modal.Secret.from_name("navya-speech")],
    min_containers=MIN_CONTAINERS,
    scaledown_window=300,  # an idle container stops after 5 minutes, so credits aren't spent between tests
    timeout=3600,  # one WebSocket lasts a whole call
)
@modal.concurrent(max_inputs=50)
@modal.asgi_app()
def web():
    from speech.stt_server.app import app as service

    return service
