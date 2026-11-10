"""Audio helpers for the TTS service: chunk conversion, resampling as audio arrives, and a speed-up that keeps pitch."""

from typing import Union

import numpy as np

from speech.stt_server.audio import float_to_pcm16, pcm16_to_float

Chunk = Union[bytes, np.ndarray]

__all__ = ["Chunk", "StreamResampler", "float_to_pcm16", "speed_up", "to_float"]


def to_float(chunk: Chunk) -> np.ndarray:
    """Engines hand back 16-bit PCM bytes, int16 arrays or float arrays; everything becomes mono float32."""
    if isinstance(chunk, (bytes, bytearray, memoryview)):
        return pcm16_to_float(bytes(chunk))
    audio = np.asarray(chunk).reshape(-1)
    if audio.dtype == np.int16:
        return audio.astype(np.float32) / 32768.0
    return audio.astype(np.float32, copy=False)


class StreamResampler:
    """One sentence's resampler. Kept across chunks, so chunk edges don't click."""

    def __init__(self, source_rate: int, target_rate: int):
        self._stream = None
        if source_rate != target_rate:
            import soxr

            self._stream = soxr.ResampleStream(source_rate, target_rate, 1, dtype="float32", quality="HQ")

    def push(self, audio: np.ndarray, last: bool = False) -> np.ndarray:
        if self._stream is None:
            return audio
        return self._stream.resample_chunk(np.ascontiguousarray(audio, dtype=np.float32), last=last)


def speed_up(audio: np.ndarray, sample_rate: int, factor: float) -> np.ndarray:
    """Faster speech at the same pitch (WSOLA): 30 ms windows are overlapped at a shorter step, each one shifted
    by up to 10 ms to where it best continues the previous one, so the waveform stays smooth."""
    if factor == 1.0 or audio.size == 0:
        return audio
    frame = max(int(sample_rate * 0.03), 16)
    hop_out = frame // 2
    hop_in = max(int(round(hop_out * factor)), 1)
    tolerance = int(sample_rate * 0.01)
    count = max((audio.size - frame) // hop_in + 1, 1)
    padded = np.concatenate([audio.astype(np.float32), np.zeros(frame + hop_out + 2 * tolerance, np.float32)])
    window = np.hanning(frame).astype(np.float32)
    out = np.zeros((count - 1) * hop_out + frame, np.float32)
    weight = np.zeros_like(out)
    position = 0
    for k in range(count):
        if k:
            natural = padded[position + hop_out:position + hop_out + frame]
            low = max(k * hop_in - tolerance, 0)
            candidates = padded[low:k * hop_in + tolerance + frame]
            position = low + int(np.argmax(np.correlate(candidates, natural, mode="valid")))
        out[k * hop_out:k * hop_out + frame] += padded[position:position + frame] * window
        weight[k * hop_out:k * hop_out + frame] += window
    weight[weight < 1e-3] = 1.0
    return (out / weight)[:int(audio.size / factor)]
