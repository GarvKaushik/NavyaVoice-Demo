"""Offline listening test for local TTS models. Never used on live calls.

Renders the same phrases as speech.prerender, so they can be compared with the Bulbul files.
Run it from the separate TTS environment (see "Local demo" in README.md):

    .venv-tts\\Scripts\\python -m speech.local_tts_render --model svara --out assets\\phrases\\local\\svara --limit 2
"""

import argparse
import os
import sys
import time
from typing import List, Optional

# Token layout shared by both models (Orpheus-style, 7 audio tokens per SNAC frame)
BOS = 128000
END_OF_TURN = 128009
START_OF_SPEECH = 128257
END_OF_SPEECH = 128258
START_OF_HUMAN = 128259
END_OF_HUMAN = 128260
START_OF_AI = 128261
END_OF_AI = 128262
SVARA_AUDIO_MARKER = 156939
AUDIO_BASE = 128266
CODEBOOK = 4096
SAMPLE_RATE = 24000
MAX_NEW_TOKENS = 2048

SVARA_GGUF = "models/svara/svara-tts-v1-Q3_K_M.gguf"
SVARA_SPEAKER = "Hindi (Female)"
VEENA_MODEL = "maya-research/veena-tts"
VEENA_SPEAKER = "kavya"


def tokens_to_codes(tokens: List[int]) -> List[int]:
    """Audio tokens to SNAC codes, dropping anything else and any incomplete last frame."""
    codes: List[int] = []
    for token in tokens:
        code = token - AUDIO_BASE - (len(codes) % 7) * CODEBOOK
        if 0 <= code < CODEBOOK:
            codes.append(code)
    return codes[:len(codes) // 7 * 7]


def split_frames(codes: List[int]) -> List[List[int]]:
    """The three SNAC levels: 1, 2 and 4 codes per frame."""
    levels: List[List[int]] = [[], [], []]
    for i in range(0, len(codes), 7):
        frame = codes[i:i + 7]
        levels[0].append(frame[0])
        levels[1] += [frame[1], frame[4]]
        levels[2] += [frame[2], frame[3], frame[5], frame[6]]
    return levels


def svara_prompt(text_tokens: List[int]) -> List[int]:
    return [BOS, START_OF_HUMAN, SVARA_AUDIO_MARKER, *text_tokens, END_OF_HUMAN, END_OF_TURN, START_OF_AI, START_OF_SPEECH]


class SvaraRenderer:
    """Compressed GGUF model through llama.cpp, which is what makes a CPU laptop practical."""

    def __init__(self, threads: int):
        from llama_cpp import Llama

        if not os.path.exists(SVARA_GGUF):
            sys.exit(f"Missing {SVARA_GGUF}. See 'Local demo' in README.md.")
        self.llm = Llama(model_path=SVARA_GGUF, n_ctx=4096, n_threads=threads, verbose=False)

    def tokens(self, text: str) -> List[int]:
        prompt = svara_prompt(self.llm.tokenize(f"{SVARA_SPEAKER}: {text}".encode(), add_bos=False, special=False))
        out: List[int] = []
        for token in self.llm.generate(prompt, temp=0.75, top_p=0.9, repeat_penalty=1.1, reset=True):
            if token in (END_OF_SPEECH, END_OF_AI) or len(out) >= MAX_NEW_TOKENS:
                break
            out.append(token)
        return out


class VeenaRenderer:
    """No GGUF build exists, so this is the full model on CPU: very slow, and it needs about 6 GB of free memory."""

    def __init__(self, threads: int):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        torch.set_num_threads(threads)
        self.torch = torch
        self.model = AutoModelForCausalLM.from_pretrained(VEENA_MODEL, torch_dtype=torch.bfloat16, trust_remote_code=True)
        self.tokenizer = AutoTokenizer.from_pretrained(VEENA_MODEL, trust_remote_code=True)

    def tokens(self, text: str) -> List[int]:
        ids = self.tokenizer.encode(f"<spk_{VEENA_SPEAKER}> {text}", add_special_tokens=False)
        prompt = [START_OF_HUMAN, *ids, END_OF_HUMAN, START_OF_AI, START_OF_SPEECH]
        with self.torch.no_grad():
            output = self.model.generate(self.torch.tensor([prompt]), max_new_tokens=min(int(len(text) * 1.3) * 7 + 21, 700),
                                         do_sample=True, temperature=0.4, top_p=0.9, repetition_penalty=1.05,
                                         eos_token_id=[END_OF_SPEECH, END_OF_AI])
        return output[0][len(prompt):].tolist()


def decode_to_audio(snac, torch, tokens: List[int]):
    levels = split_frames(tokens_to_codes(tokens))
    if not levels[0]:
        return None
    with torch.no_grad():
        audio = snac.decode([torch.tensor([level], dtype=torch.int32) for level in levels])
    return audio.squeeze().clamp(-1, 1).numpy()


def main(model: str, out_dir: str, limit: Optional[int], threads: int, devanagari: bool = False) -> int:
    import soundfile as sf
    import torch
    from snac import SNAC

    from speech.devanagari import to_devanagari
    from speech.phrase_cache import PreRenderedPhraseCache

    renderer = SvaraRenderer(threads) if model == "svara" else VeenaRenderer(threads)
    snac = SNAC.from_pretrained("hubertsiuzdak/snac_24khz").eval()
    phrases = PreRenderedPhraseCache()
    os.makedirs(out_dir, exist_ok=True)
    timings = []
    for phrase_id in list(phrases.CANONICAL_TEXTS)[:limit]:
        text = phrases.get_text(phrase_id)
        if devanagari:
            text = to_devanagari(text)
        started = time.monotonic()
        tokens = renderer.tokens(text)
        audio = decode_to_audio(snac, torch, tokens)
        elapsed = time.monotonic() - started
        if audio is None:
            print(f"  {phrase_id:<26} no audio produced ({len(tokens)} tokens)")
            continue
        path = os.path.join(out_dir, f"{phrase_id}.wav")
        sf.write(path, audio, SAMPLE_RATE)
        seconds = audio.size / SAMPLE_RATE
        timings.append(elapsed)
        print(f"  {phrase_id:<26} {seconds:5.1f}s audio in {elapsed:6.1f}s ({elapsed / seconds:.1f}x real time)")
    if timings:
        print(f"Average {sum(timings) / len(timings):.1f}s per phrase. Files are in {out_dir}")
    return 0 if timings else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=["svara", "veena"], required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=None, help="render only the first N phrases")
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--devanagari", action="store_true", help="convert Roman Hinglish to Devanagari first")
    args = ap.parse_args()
    sys.exit(main(args.model, args.out, args.limit, args.threads, args.devanagari))
