"""A quick listen to a score: melody, instrument line and chords as plain tones.

This is for checking notes before spending minutes on a real song, not for
sound quality. Pure Python (the window ships without numpy), so it leans on
work the interpreter does in C:

* each pitch gets one precomputed cycle; a sustained note is that cycle
  repeated (list multiplication) rather than computed sample by sample;
* each voice is monophonic, so it renders into its own buffer by slice
  assignment; only chords (several tones at once) and the final three-way mix
  touch every sample in Python.

A three-minute score renders in a few seconds at 16 kHz.
"""
from __future__ import annotations

import io
import math
import wave
from array import array

from app.score import theory
from app.score.model import Score

RATE = 16000
ATTACK = int(RATE * 0.012)
RELEASE = int(RATE * 0.05)
LEVELS = {"Vocal": 0.42, "Ins": 0.22, "chords": 0.10}


def _cycle(pitch: int, shape: str) -> list[float]:
    frequency = 440.0 * 2 ** ((pitch - 69) / 12)
    period = max(2, round(RATE / frequency))
    if shape == "triangle":
        return [1 - 4 * abs(i / period - 0.5) for i in range(period)]
    if shape == "square":
        return [0.6 if i < period // 2 else -0.6 for i in range(period)]
    return [math.sin(2 * math.pi * i / period) for i in range(period)]


class _Tables:
    def __init__(self):
        self._cache: dict[tuple[int, str], list[float]] = {}

    def get(self, pitch: int, shape: str) -> list[float]:
        key = (pitch, shape)
        if key not in self._cache:
            self._cache[key] = _cycle(pitch, shape)
        return self._cache[key]


def _tone(table: list[float], count: int) -> list[float]:
    """``count`` samples of a repeated cycle with a short attack and release."""
    if count <= 0:
        return []
    repeats = count // len(table) + 1
    samples = (table * repeats)[:count]
    attack = min(ATTACK, count // 2)
    release = min(RELEASE, count - attack)
    for i in range(attack):
        samples[i] *= i / attack
    for i in range(release):
        samples[count - 1 - i] *= i / release
    return samples


def render(score: Score, rate_check: bool = False) -> bytes:
    """A mono 16-bit WAV of the whole score."""
    seconds_per_quarter = 60 / score.bpm
    total = int(float(score.length) * seconds_per_quarter * RATE) + RATE // 4
    tables = _Tables()

    def index(time) -> int:
        return int(float(time) * seconds_per_quarter * RATE)

    buffers = {}
    for voice, shape in (("Vocal", "triangle"), ("Ins", "square")):
        buffer = [0.0] * total
        for note in score.voices[voice]:
            start, end = index(note.onset), min(total, index(note.end))
            buffer[start:end] = _tone(tables.get(note.pitch, shape), end - start)
        buffers[voice] = buffer

    pad = [0.0] * total
    chords = sorted(score.chords)
    for i, (onset, symbol) in enumerate(chords):
        end_time = chords[i + 1][0] if i + 1 < len(chords) else score.length
        start, end = index(onset), min(total, index(end_time))
        try:
            pitches = theory.chord_pitches(symbol)
        except ValueError:
            continue
        tones = [_tone(tables.get(p, "sine"), end - start) for p in pitches]
        pad[start:end] = [sum(values) / len(tones) for values in zip(*tones)]

    v, s, c = LEVELS["Vocal"], LEVELS["Ins"], LEVELS["chords"]
    mixed = array("h", (max(-32767, min(32767, int((a * v + b * s + p * c) * 32767)))
                        for a, b, p in zip(buffers["Vocal"], buffers["Ins"], pad)))
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(mixed.tobytes())
    return out.getvalue()
