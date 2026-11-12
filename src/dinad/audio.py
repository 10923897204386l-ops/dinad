"""Validation of float WAV output shared by native renderers."""

import math
from pathlib import Path
import struct


def inspect_float_wav(path: Path, sample_rate: int, expected_duration: float, *,
                require_signal: bool = True) -> dict:
    """Check actual float32 WAV output without requiring ffmpeg or an audio device."""
    with path.open("rb") as stream:
        header = stream.read(12)
        if len(header) != 12 or header[:4] != b"RIFF" or header[8:] != b"WAVE":
            raise ValueError("render did not produce a RIFF WAV file")
        audio_format = None
        while True:
            chunk = stream.read(8)
            if len(chunk) != 8:
                raise ValueError("rendered WAV has no complete audio data")
            name, size = struct.unpack("<4sI", chunk)
            if name == b"fmt ":
                if size < 16:
                    raise ValueError("rendered WAV has an invalid format chunk")
                raw_format = stream.read(16)
                if len(raw_format) != 16:
                    raise ValueError("rendered WAV format chunk is truncated")
                audio_format = struct.unpack("<HHIIHH", raw_format)
                stream.seek(size - 16 + size % 2, 1)
            elif name == b"data":
                break
            else:
                stream.seek(size + size % 2, 1)
        if audio_format != (3, 2, sample_rate, sample_rate * 8, 8, 32):
            raise ValueError("rendered WAV must be stereo float32 at the requested sample rate")
        if not size or size % 8:
            raise ValueError("rendered WAV has invalid audio data length")
        duration = size / (sample_rate * 8)
        if abs(duration - expected_duration) > 128 / sample_rate:
            raise ValueError("rendered WAV duration differs from the compiled schedule")
        remaining, peak, energy = size, 0.0, 0.0
        while remaining:
            data = stream.read(min(65536, remaining))
            if not data or len(data) % 4:
                raise ValueError("rendered WAV audio is truncated")
            remaining -= len(data)
            for (sample,) in struct.iter_unpack("<f", data):
                if not math.isfinite(sample):
                    raise ValueError("rendered WAV contains non-finite samples")
                peak = max(peak, abs(sample))
                energy += sample * sample
        if require_signal and peak == 0:
            raise ValueError("rendered WAV is silent despite non-empty event history")
        if peak >= 1:
            raise ValueError("rendered WAV reaches or exceeds full scale")
        return {"duration_seconds": duration, "peak": peak,
                "rms": math.sqrt(energy / (size / 4))}
