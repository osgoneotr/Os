"""Audio feature extraction.

We decode the whole track once to 16 kHz mono PCM through an ffmpeg pipe and
compute an RMS envelope with numpy. That is deliberately lighter than librosa —
loudness and its variation are the only acoustic signals the scorer uses, and
they do not justify a 200MB dependency tree.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .ffmpeg import run_ffmpeg_pipe

DEFAULT_SR = 16_000


def load_pcm(path: str | Path, sample_rate: int = DEFAULT_SR) -> np.ndarray:
    """Decode to mono float32 in [-1, 1]."""
    raw = run_ffmpeg_pipe(
        [
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-",
        ]
    )
    if not raw:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


@dataclass
class AudioEnvelope:
    """RMS loudness sampled on a fixed grid, plus whole-video statistics used to
    normalize per-window features."""

    times: np.ndarray
    rms: np.ndarray
    hop: float
    sample_rate: int

    @property
    def mean(self) -> float:
        return float(self.rms.mean()) if self.rms.size else 0.0

    @property
    def std(self) -> float:
        return float(self.rms.std()) if self.rms.size else 0.0

    def slice(self, start: float, end: float) -> np.ndarray:
        i0 = int(max(0.0, start) / self.hop)
        i1 = int(max(start, end) / self.hop)
        return self.rms[i0 : max(i1, i0 + 1)]

    def window_stats(self, start: float, end: float) -> tuple[float, float, float]:
        """(mean, std, peak) RMS over a window."""
        chunk = self.slice(start, end)
        if chunk.size == 0:
            return 0.0, 0.0, 0.0
        return float(chunk.mean()), float(chunk.std()), float(chunk.max())

    def silence_ratio(self, start: float, end: float, threshold: float | None = None) -> float:
        """Fraction of the window below a silence threshold, defaulting to 15% of
        the video's mean loudness."""
        chunk = self.slice(start, end)
        if chunk.size == 0:
            return 1.0
        thr = threshold if threshold is not None else max(1e-4, self.mean * 0.15)
        return float((chunk < thr).mean())


def rms_envelope(
    pcm: np.ndarray, sample_rate: int = DEFAULT_SR, hop: float = 0.05
) -> AudioEnvelope:
    """Frame the signal into ``hop``-second windows and take RMS of each."""
    hop_samples = max(1, int(sample_rate * hop))
    if pcm.size == 0:
        return AudioEnvelope(np.zeros(0), np.zeros(0), hop, sample_rate)
    n_frames = int(np.ceil(pcm.size / hop_samples))
    padded = np.pad(pcm, (0, n_frames * hop_samples - pcm.size))
    frames = padded.reshape(n_frames, hop_samples)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1)).astype(np.float32)
    times = np.arange(n_frames, dtype=np.float32) * hop
    return AudioEnvelope(times, rms, hop, sample_rate)


def envelope_for(path: str | Path, hop: float = 0.05) -> AudioEnvelope:
    pcm = load_pcm(path)
    return rms_envelope(pcm, DEFAULT_SR, hop)
