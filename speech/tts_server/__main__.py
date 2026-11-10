"""Starts the TTS service.

    python -m speech.tts_server                    # http://127.0.0.1:8766, voices from TTS_ENGINE / TTS_FALLBACK_ENGINE
    python -m speech.tts_server --host 0.0.0.0     # reachable from other machines: set SPEECH_SERVER_TOKEN first
"""

import argparse
import logging

import uvicorn

from config.settings import get_settings

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--log-level", default="warning", choices=["warning", "info", "debug"],
                    help="info also logs each connection; the server's own messages always show")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.host not in ("127.0.0.1", "localhost") and not get_settings().SPEECH_SERVER_TOKEN:
        logging.warning("SPEECH_SERVER_TOKEN is empty: anyone who can reach this server can use it")
    uvicorn.run("speech.tts_server.app:app", host=args.host, port=args.port, log_level=args.log_level)
