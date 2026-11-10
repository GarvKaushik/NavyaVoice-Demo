"""Compares STT engines on your own recordings.

    python -m scripts.stt_bakeoff --audio samples --refs samples --engines indicconformer,whisper_hinglish

--audio holds WAV files; --refs holds transcripts with the same name and a .txt ending.
Writes a CSV with transcript, WER, CER, processing time and real-time factor, then a summary per engine.
"""

import argparse
import asyncio
import csv
import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SHORT_ANSWER_MAX_WORDS = 5

# Spellings of the same word, so scoring measures hearing and not romanization habits
_SPELLINGS = {"han": "haan", "ha": "haan", "haa": "haan", "hanji": "haan ji", "haanji": "haan ji", "nahin": "nahi",
              "nhi": "nahi", "ok": "okay", "achha": "accha", "acha": "accha", "thik": "theek", "dhanyavad": "dhanyawaad",
              "sham": "shaam", "bajey": "baje", "whatsapp": "whatsapp", "main": "main", "mai": "main", "hun": "hoon",
              "hu": "hoon", "ye": "yeh", "wo": "woh", "vo": "woh"}


def normalize_text(text: str) -> str:
    """The same steps for every engine before scoring: Roman script, lower case, no punctuation, one spelling."""
    from speech.lexicon import apply_lexicon
    from speech.transliterate import to_roman

    text = apply_lexicon(to_roman(unicodedata.normalize("NFC", text or ""))).lower()
    text = re.sub(r"[^\w\s:]", " ", text).replace("_", " ")
    return " ".join(_SPELLINGS.get(word, word) for word in text.split())


def score(reference: str, hypothesis: str) -> Dict[str, float]:
    import jiwer

    ref, hyp = normalize_text(reference), normalize_text(hypothesis)
    if not ref:
        return {"wer": 0.0 if not hyp else 1.0, "cer": 0.0 if not hyp else 1.0}
    if not hyp:
        return {"wer": 1.0, "cer": 1.0}
    return {"wer": jiwer.wer(ref, hyp), "cer": jiwer.cer(ref, hyp)}


def is_short_answer(reference: str) -> bool:
    return 0 < len(normalize_text(reference).split()) <= SHORT_ANSWER_MAX_WORDS


@dataclass
class Row:
    file: str
    engine: str
    transcript: str
    reference: Optional[str]
    wer: Optional[float]
    cer: Optional[float]
    seconds: float
    audio_seconds: float

    @property
    def rtf(self) -> float:
        return self.seconds / self.audio_seconds if self.audio_seconds else 0.0


def make_row(file: str, engine: str, transcript: str, reference: Optional[str], seconds: float, audio_seconds: float) -> Row:
    scores = score(reference, transcript) if reference is not None else {"wer": None, "cer": None}
    return Row(file, engine, transcript, reference, scores["wer"], scores["cer"], seconds, audio_seconds)


def summarize(rows: List[Row]) -> List[Dict[str, object]]:
    summary = []
    for engine in dict.fromkeys(r.engine for r in rows):
        mine = [r for r in rows if r.engine == engine]
        scored = [r for r in mine if r.wer is not None]
        short = [r for r in scored if is_short_answer(r.reference)]
        exact = sum(normalize_text(r.reference) == normalize_text(r.transcript) for r in short)
        total_audio = sum(r.audio_seconds for r in mine)
        summary.append({
            "engine": engine, "files": len(mine),
            "wer": sum(r.wer for r in scored) / len(scored) if scored else None,
            "cer": sum(r.cer for r in scored) / len(scored) if scored else None,
            "rtf": sum(r.seconds for r in mine) / total_audio if total_audio else 0.0,
            "short_answers": len(short), "short_exact": exact,
        })
    return summary


def read_audio(path: str):
    """Returns (16 kHz mono float audio, the file's own 8 or 16 kHz mono float audio, its rate)."""
    import numpy as np
    import soundfile as sf

    from speech.stt_server.audio import resample

    audio, rate = sf.read(path, dtype="float32", always_2d=True)
    mono = np.mean(audio, axis=1)
    return resample(mono, rate), mono, rate


async def run_sarvam(mono, rate: int) -> str:
    from speech.local_stt import decode_on_sarvam
    from speech.stt_server.audio import float_to_pcm16

    return await decode_on_sarvam(float_to_pcm16(mono), rate)


async def run_engine(name: str, files: List[str], refs: Dict[str, str]) -> List[Row]:
    from speech.stt_server.audio import TARGET_RATE, trim_silence
    from speech.stt_server.engines import EngineUnavailable, create_engine

    engine = None
    if name != "sarvam":
        try:
            engine = create_engine(name)
            started = time.monotonic()
            engine.load()  # one model at a time: this one is released before the next engine loads
            print(f"{name}: loaded in {time.monotonic() - started:.1f}s")
        except EngineUnavailable as e:
            print(f"{name}: skipped. {e}")
            return []
    rows = []
    for path in files:
        audio16k, mono, rate = read_audio(path)
        base = os.path.splitext(os.path.basename(path))[0]
        started = time.monotonic()
        if engine:
            speech = trim_silence(audio16k, TARGET_RATE)
            text = engine.transcribe(speech) if speech.size else ""
        else:
            text = await run_sarvam(mono, rate)
        rows.append(make_row(os.path.basename(path), name, text, refs.get(base), time.monotonic() - started,
                             mono.size / rate))
    if engine:
        engine.close()
    return rows


def load_refs(folder: Optional[str]) -> Dict[str, str]:
    refs: Dict[str, str] = {}
    if folder and os.path.isdir(folder):
        for entry in os.listdir(folder):
            if entry.lower().endswith(".txt"):
                with open(os.path.join(folder, entry), encoding="utf-8-sig") as f:
                    refs[os.path.splitext(entry)[0]] = f.read().strip()
    return refs


def write_csv(path: str, rows: List[Row]) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["file", "engine", "transcript", "reference", "wer", "cer", "processing_seconds",
                         "audio_seconds", "real_time_factor"])
        for r in rows:
            writer.writerow([r.file, r.engine, r.transcript, r.reference or "",
                             "" if r.wer is None else round(r.wer, 4), "" if r.cer is None else round(r.cer, 4),
                             round(r.seconds, 3), round(r.audio_seconds, 3), round(r.rtf, 3)])


def print_summary(summary: List[Dict[str, object]]) -> None:
    print(f"\n{'engine':<20}{'files':>6}{'WER':>8}{'CER':>8}{'RTF':>8}   short answers exact")
    for s in summary:
        wer = "-" if s["wer"] is None else f"{s['wer']:.1%}"
        cer = "-" if s["cer"] is None else f"{s['cer']:.1%}"
        short = f"{s['short_exact']}/{s['short_answers']}" if s["short_answers"] else "-"
        print(f"{s['engine']:<20}{s['files']:>6}{wer:>8}{cer:>8}{s['rtf']:>8.2f}   {short}")
    print("RTF below 1.0 means faster than real time.")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True)
    ap.add_argument("--refs")
    ap.add_argument("--engines", default="indicconformer,whisper_hinglish", help="add sarvam to compare with it")
    ap.add_argument("--out", default="stt_bakeoff.csv")
    args = ap.parse_args()

    files = sorted(os.path.join(args.audio, f) for f in os.listdir(args.audio) if f.lower().endswith(".wav"))
    if not files:
        sys.exit(f"No WAV files in {args.audio}")
    refs = load_refs(args.refs)
    rows: List[Row] = []
    for name in [e.strip() for e in args.engines.split(",") if e.strip()]:
        rows += await run_engine(name, files, refs)
    write_csv(args.out, rows)
    print_summary(summarize(rows))
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
