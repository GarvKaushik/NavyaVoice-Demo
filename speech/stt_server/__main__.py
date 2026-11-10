"""Starts the local STT server.

    python -m speech.stt_server
    python -m speech.stt_server --port 8765 --host 127.0.0.1
    python -m speech.stt_server --host 0.0.0.0     # reachable from other machines: set SPEECH_SERVER_TOKEN first
    python -m speech.stt_server --log-level info   # also log every connection (accepted, or 403 for a bad token)
"""

import argparse
import logging

import uvicorn

from config.settings import get_settings

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--log-level", default="warning", choices=["warning", "info", "debug"],
                    help="info also logs each connection; the server's own messages always show")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.host not in ("127.0.0.1", "localhost") and not get_settings().SPEECH_SERVER_TOKEN:
        logging.warning("SPEECH_SERVER_TOKEN is empty: anyone who can reach this server can use it")
    uvicorn.run("speech.stt_server.app:app", host=args.host, port=args.port, log_level=args.log_level)
