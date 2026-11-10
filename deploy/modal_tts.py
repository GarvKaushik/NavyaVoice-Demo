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
