"""Renders every scripted phrase with the selected TTS provider once and stores it in the phrase cache.

    python -m speech.prerender            # render missing phrases
    python -m speech.prerender --force    # re-render all (after changing voice, pitch, pace or text)
    python -m speech.prerender --wav out  # also write .wav copies to listen to
"""

import argparse
import asyncio
import os
import sys
import time

from speech.bulbul_tts import pcm_to_wav
from speech.openai_tts import create_tts_client
from speech.phrase_cache import PreRenderedPhraseCache

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


async def main(force: bool, wav_dir: str) -> int:
    cache = PreRenderedPhraseCache()
    tts = create_tts_client()
    if not tts.configured:
        print("The API key for the selected TTS provider is not set")
        return 1
    print(f"voice={tts.voice_id} model={tts.model} rate={tts.sample_rate} Hz -> {cache.cache_dir}")
    failures = 0
    for phrase_id in cache.CANONICAL_TEXTS:
        path = cache.path_for(phrase_id)
        text = cache.get_text(phrase_id)
        if os.path.exists(path) and not force:
            print(f"  cached   {phrase_id}")
            pcm = open(path, "rb").read()
        else:
            t0 = time.monotonic()
            try:
                pcm = await tts.synthesize_pcm(text)
            except Exception as e:
                failures += 1
                print(f"  FAILED   {phrase_id}: {e}")
                continue
            cache.save(phrase_id, pcm)
            secs = len(pcm) / 2 / tts.sample_rate
            print(f"  rendered {phrase_id:<26} {secs:4.1f}s audio in {(time.monotonic() - t0) * 1000:.0f} ms")
        if wav_dir:
            os.makedirs(wav_dir, exist_ok=True)
            with open(os.path.join(wav_dir, f"{phrase_id}.wav"), "wb") as f:
                f.write(pcm_to_wav(pcm, tts.sample_rate))
    return 1 if failures else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--wav", default="", help="directory to write listenable .wav copies")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.force, args.wav)))
