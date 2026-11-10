"""Audio helpers shared by the STT server, the adapter's fallback and the bake-off."""

import numpy as np

TARGET_RATE = 16000
_FRAME_SECONDS = 0.02
_PADDING_SECONDS = 0.2
_MIN_SPEECH_SECONDS = 0.1
_ABSOLUTE_THRESHOLD = 0.006  # a quiet line stays below this; speech rises well above it


def pcm16_to_float(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def float_to_pcm16(audio: np.ndarray) -> bytes:
    return (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()


def resample(audio: np.ndarray, source_rate: int, target_rate: int = TARGET_RATE) -> np.ndarray:
    if source_rate == target_rate or audio.size == 0:
        return audio
    import soxr

    return soxr.resample(audio, source_rate, target_rate, quality="HQ")


def trim_silence(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    """Keeps the span from first to last speech. Silence makes Whisper invent words, and this also saves decode time.
    Returns an empty array when nothing sounds like speech."""
    frame = max(int(sample_rate * _FRAME_SECONDS), 1)
    count = audio.size // frame
    if count == 0:
        return audio[:0]
    energy = np.sqrt(np.mean(audio[:count * frame].reshape(count, frame) ** 2, axis=1))
    threshold = max(_ABSOLUTE_THRESHOLD, float(np.percentile(energy, 10)) * 3.0)
    active = np.flatnonzero(energy > threshold)
    if active.size * _FRAME_SECONDS < _MIN_SPEECH_SECONDS:
        return audio[:0]
    padding = int(_PADDING_SECONDS / _FRAME_SECONDS)
    start = max(int(active[0]) - padding, 0) * frame
    end = min(int(active[-1]) + padding + 1, count) * frame
    return audio[start:end]
