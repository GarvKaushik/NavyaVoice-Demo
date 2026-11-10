"""Checks a running STT or TTS service by its link, using SPEECH_SERVER_TOKEN from .env.

    python -m scripts.speech_check stt --url wss://<host>/v1/stt                        # speak 5 s into the microphone
    python -m scripts.speech_check stt --url wss://<host>/v1/stt --text "Price kya hai?"  # a typed sentence (Piper says it)
    python -m scripts.speech_check stt --url wss://<host>/v1/stt --wav speech.wav       # a 16-bit mono WAV
    python -m scripts.speech_check tts --url wss://<host>/v1/tts --text "Namaste ji"    # writes tts_check.wav

Every step is printed, so a failure shows where it happened.
"""

import argparse
import asyncio
import sys
import time
import wave

import aiohttp

from config.settings import get_settings
from speech.service_auth import auth_headers
from speech.stt_server.audio import float_to_pcm16, pcm16_to_float, resample

STT_RATE = 8000
_started = time.monotonic()


def step(message: str) -> None:
    print(f"[{time.monotonic() - _started:6.2f}s] {message}", flush=True)


def _speech_for_stt(args) -> bytes:
    """16-bit mono PCM at 8 kHz, with a little silence on both sides."""
    if args.wav:
        with wave.open(args.wav, "rb") as w:
            if w.getsampwidth() != 2 or w.getnchannels() != 1:
                sys.exit("The WAV must be 16-bit mono")
            rate, pcm = w.getframerate(), w.readframes(w.getnframes())
    elif args.text:
        from speech.piper_tts import render

        rate, pcm = get_settings().PIPER_SAMPLE_RATE, render(args.text)
    else:
        import sounddevice as sd

        input(f"Press Enter, then speak for {args.seconds:g} seconds...")
        step("recording")
        recording = sd.rec(int(args.seconds * 16000), samplerate=16000, channels=1, dtype="int16")
        sd.wait()
        rate, pcm = 16000, recording.tobytes()
    audio = float_to_pcm16(resample(pcm16_to_float(pcm), rate, STT_RATE))
    silence = b"\0\0" * (STT_RATE // 4)
    return silence + audio + silence


async def check_stt(args) -> None:
    audio = _speech_for_stt(args)
    headers = {**auth_headers(), "ngrok-skip-browser-warning": "true"}
    step(f"token from .env: {'set' if 'Authorization' in headers else 'MISSING'}")
    step(f"connecting to {args.url}")
    async with aiohttp.ClientSession() as http:
        try:
            ws = await http.ws_connect(args.url, headers=headers, max_msg_size=0)
        except aiohttp.WSServerHandshakeError as e:
            reason = {403: "wrong or missing token",
                      404: "nothing at this link: is the server and its tunnel running, and does it end in /v1/stt?"}
            step(f"REJECTED with HTTP {e.status}: {reason.get(e.status, 'check the link')}")
            return
        except aiohttp.ClientError as e:
            step(f"could not connect: {e!r}")
            return
        step("connected")
        await ws.send_json({"type": "config", "engine": args.engine, "sample_rate": STT_RATE})
        for i in range(0, len(audio), STT_RATE // 50 * 2):  # 20 ms frames, as a live call sends them
            await ws.send_bytes(audio[i:i + STT_RATE // 50 * 2])
        await ws.send_json({"type": "flush"})
        step(f"sent config, {len(audio) / 2 / STT_RATE:.1f}s of audio and flush; waiting "
             "(the first request after a start loads the model and can take ~20 s)")
        try:
            reply = await asyncio.wait_for(ws.receive_json(), timeout=args.timeout)
            step(f"reply: {reply}")
        except asyncio.TimeoutError:
            step(f"no reply within {args.timeout:g}s")
        await ws.close()


async def check_tts(args) -> None:
    from speech.server_tts import ServerTTSError, TTSConnection

    step(f"token from .env: {'set' if auth_headers() else 'MISSING'}")
    step(f"connecting to {args.url}")
    connection = TTSConnection(url=args.url, sample_rate=args.rate)
    audio = bytearray()
    first = None
    try:
        async for piece in connection.speak(args.text):
            first = first or time.monotonic()
            audio += piece
    except ServerTTSError as e:
        step(f"FAILED: {e} ({'wrong or missing token?' if '403' in str(e) else 'see the TTS server log'})")
        return
    finally:
        await connection.aclose()
    with wave.open(args.out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(args.rate)
        w.writeframes(bytes(audio))
    voice = connection.last_engine + (" (fallback voice)" if connection.last_fallback else "")
    step(f"spoken by {voice}: {len(audio) / 2 / args.rate:.1f}s of audio, written to {args.out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="service", required=True)
    stt = sub.add_parser("stt", help="send speech, print the transcript")
    stt.add_argument("--url", required=True, help="wss://<host>/v1/stt")
    stt.add_argument("--text", help="a typed sentence for Piper to say, instead of the microphone")
    stt.add_argument("--wav", help="a 16-bit mono WAV file, instead of the microphone")
    stt.add_argument("--seconds", type=float, default=5, help="microphone recording length")
    stt.add_argument("--engine", default="indicconformer")
    stt.add_argument("--timeout", type=float, default=60)
    tts = sub.add_parser("tts", help="send a sentence, save the audio")
    tts.add_argument("--url", required=True, help="wss://<host>/v1/tts")
    tts.add_argument("--text", default="Namaste ji, main Priya bol rahi hoon. Kya aap abhi baat kar sakte hain?")
    tts.add_argument("--rate", type=int, default=8000)
    tts.add_argument("--out", default="tts_check.wav")
    args = ap.parse_args()
    asyncio.run(check_stt(args) if args.service == "stt" else check_tts(args))


if __name__ == "__main__":
    main()
