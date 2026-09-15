"""An editable score: notes, chords and sections on a bar grid.

Times are exact ``Fraction``s of a quarter note, as in the vendored parser, so
nothing drifts through edit / save / reload. Both voices are monophonic (the
native format's rule): placing a note trims or removes whatever it overlaps in
the same voice.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from fractions import Fraction

from app.score import abc_dialect as dialect
from app.score import theory

VOICES = dialect.VOICES          # ("Vocal", "Ins")
F = Fraction


class UnsupportedScore(ValueError):
    """Valid for YuE2 but outside what the visual editor handles."""


@dataclass
class Note:
    onset: Fraction
    pitch: int
    duration: Fraction

    @property
    def end(self) -> Fraction:
        return self.onset + self.duration


@dataclass
class Score:
    """``meters`` holds one metre per bar: YuE2's plans occasionally slip in a
    single 9/8 or 2/4 bar, so bars cannot be assumed to be the same length."""
    bpm: int = 90
    key: str = "C"                                   # the key the song starts in
    meters: list[tuple[int, int]] = field(default_factory=lambda: [(4, 4)] * 8)
    #: Later key changes as (bar, key). Recordings often modulate (SheetSage2
    #: writes e.g. A major -> F# major). A key only decides how notes are spelled,
    #: never their pitch, so a change inside a bar is moved to the next barline.
    key_changes: list[tuple[int, str]] = field(default_factory=list)
    sections: list[tuple[int, str]] = field(default_factory=lambda: [(0, "verse")])
    chords: list[tuple[Fraction, str]] = field(default_factory=list)
    voices: dict[str, list[Note]] = field(default_factory=lambda: {v: [] for v in VOICES})

    @classmethod
    def blank(cls, bars: int = 8, meter: tuple[int, int] = (4, 4), **fields) -> "Score":
        return cls(meters=[tuple(meter)] * bars, **fields)

    # --- geometry --------------------------------------------------------
    @property
    def bars(self) -> int:
        return len(self.meters)

    @property
    def meter(self) -> tuple[int, int]:
        return self.meters[0] if self.meters else (4, 4)

    def key_at(self, bar: int) -> str:
        key = self.key
        for start, name in sorted(self.key_changes):
            if start <= bar:
                key = name
        return key

    def bar_length_at(self, bar: int) -> Fraction:
        n, d = self.meters[bar]
        return F(4 * n, d)

    def bar_start(self, bar: int) -> Fraction:
        """Start time of ``bar``; ``bar == bars`` gives the end of the score."""
        return sum((self.bar_length_at(i) for i in range(min(bar, self.bars))), F(0))

    @property
    def bar_starts(self) -> list[Fraction]:
        starts, time = [], F(0)
        for i in range(self.bars):
            starts.append(time)
            time += self.bar_length_at(i)
        return starts + [time]

    @property
    def bar_length(self) -> Fraction:
        """Length of the first bar (the score's main metre)."""
        return self.bar_length_at(0)

    @property
    def length(self) -> Fraction:
        return self.bar_start(self.bars)

    @property
    def seconds(self) -> float:
        return float(self.length) * 60 / self.bpm

    def bar_of(self, time: Fraction) -> int:
        starts = self.bar_starts
        for bar in range(self.bars):
            if time < starts[bar + 1]:
                return bar
        return max(0, self.bars - 1)

    def section_at(self, bar: int) -> str:
        name = ""
        for start, section in sorted(self.sections):
            if start <= bar:
                name = section
        return name

    def pitch_range(self, voice: str = "Vocal") -> tuple[int, int] | None:
        pitches = [n.pitch for n in self.voices[voice]]
        return (min(pitches), max(pitches)) if pitches else None

    # --- loading ---------------------------------------------------------
    @classmethod
    def from_abc(cls, text: str) -> "Score":
        parsed = dialect.parse(text)
        vocal = parsed.voices["Vocal"]
        score = cls(bpm=parsed.bpm, key=vocal.keys[0][1], meters=[tuple(bar[2]) for bar in vocal.bars],
                    sections=list(parsed.sections) or [(0, "verse")],
                    chords=[(F(t), c) for t, c in vocal.chords],
                    voices={name: [Note(F(t), p, F(d)) for t, p, d in parsed.voices[name].notes]
                            for name in VOICES})
        changes: dict[int, str] = {}
        for time, name in vocal.keys[1:]:
            bar = score.bar_of(F(time))
            if F(time) > score.bar_start(bar):      # mid-bar: spell from the next barline
                bar += 1
            if bar < score.bars:
                changes[bar] = name
        current, kept = score.key, []
        for bar in sorted(changes):
            if bar == 0:
                score.key = current = changes[bar]
            elif changes[bar] != current:
                kept.append((bar, changes[bar]))
                current = changes[bar]
        score.key_changes = kept
        return score

    def copy(self) -> "Score":
        return copy.deepcopy(self)

    # --- editing: notes --------------------------------------------------
    def _clear(self, voice: str, start: Fraction, end: Fraction, keep: Note | None = None) -> None:
        """Make [start, end) silent in ``voice`` by trimming, splitting or removing notes."""
        result = []
        for note in self.voices[voice]:
            if note is keep or note.end <= start or note.onset >= end:
                result.append(note)
                continue
            if note.onset < start:
                result.append(Note(note.onset, note.pitch, start - note.onset))
            if note.end > end:
                result.append(Note(end, note.pitch, note.end - end))
        self.voices[voice] = sorted(result, key=lambda n: n.onset)

    def add_note(self, voice: str, onset: Fraction, pitch: int, duration: Fraction) -> Note:
        onset = max(F(0), F(onset))
        end = min(self.length, onset + F(duration))
        if end <= onset:
            raise ValueError("A note needs a length inside the score")
        if not 0 <= pitch <= 127:
            raise ValueError("Pitch outside MIDI range")
        note = Note(onset, int(pitch), end - onset)
        self._clear(voice, note.onset, note.end)
        self.voices[voice].append(note)
        self.voices[voice].sort(key=lambda n: n.onset)
        return note

    def delete_note(self, voice: str, note: Note) -> None:
        self.voices[voice] = [n for n in self.voices[voice] if n is not note]

    def move_note(self, voice: str, note: Note, onset: Fraction, pitch: int) -> Note:
        self.delete_note(voice, note)
        return self.add_note(voice, onset, pitch, note.duration)

    def resize_note(self, voice: str, note: Note, duration: Fraction) -> Note:
        self.delete_note(voice, note)
        return self.add_note(voice, note.onset, note.pitch, duration)

    def notes_in(self, voice: str, start: Fraction, end: Fraction) -> list[Note]:
        return [n for n in self.voices[voice] if n.onset < end and n.end > start]

    def note_at(self, voice: str, time: Fraction, pitch: int) -> Note | None:
        for note in self.voices[voice]:
            if note.pitch == pitch and note.onset <= time < note.end:
                return note
        return None

    # --- editing: chords, sections, bars -----------------------------------
    def set_chord(self, onset: Fraction, symbol: str) -> None:
        theory.parse_chord(symbol)                 # raises on anything unsupported
        if not dialect.CHORD.fullmatch(symbol):
            raise ValueError(f"Unsupported chord {symbol!r}")
        onset = F(onset)
        if not 0 <= onset < self.length:
            raise ValueError("Chord outside the score")
        self.chords = sorted([(t, c) for t, c in self.chords if t != onset] + [(onset, symbol)])

    def delete_chord(self, onset: Fraction) -> None:
        self.chords = [(t, c) for t, c in self.chords if t != F(onset)]

    def chord_at(self, time: Fraction) -> tuple[Fraction, str] | None:
        current = None
        for onset, symbol in self.chords:
            if onset <= time:
                current = (onset, symbol)
        return current

    def set_section(self, bar: int, name: str) -> None:
        name = " ".join(name.strip().lower().split())
        others = [(b, n) for b, n in self.sections if b != bar]
        self.sections = sorted(others + ([(bar, name)] if name else []))
        if not any(b == 0 for b, _ in self.sections):
            self.sections.insert(0, (0, self.sections[0][1] if self.sections else "verse"))

    def add_bars(self, count: int = 1, at: int | None = None) -> None:
        """Insert empty bars before bar ``at`` (default: at the end)."""
        at = self.bars if at is None else max(0, min(at, self.bars))
        meter = self.meters[at - 1] if at > 0 else self.meter
        shift = F(4 * meter[0], meter[1]) * count
        start = self.bar_start(at)
        for voice in VOICES:
            self._split_at(voice, start)
            for note in self.voices[voice]:
                if note.onset >= start:
                    note.onset += shift
        self.chords = [(t + shift if t >= start else t, c) for t, c in self.chords]
        self.sections = [(b + count if b >= at and b > 0 else b, n) for b, n in self.sections]
        self.key_changes = [(b + count if b >= at else b, k) for b, k in self.key_changes]
        self.meters[at:at] = [meter] * count

    def delete_bar(self, bar: int) -> None:
        if self.bars <= 1:
            raise ValueError("A score needs at least one bar")
        start, end = self.bar_start(bar), self.bar_start(bar + 1)
        length = end - start
        for voice in VOICES:
            self._clear(voice, start, end)
            for note in self.voices[voice]:
                if note.onset >= end:
                    note.onset -= length
        self.chords = [(t - length if t >= end else t, c) for t, c in self.chords
                       if not start <= t < end]
        sections = []
        for b, n in self.sections:
            if b == bar and b != 0:
                continue
            sections.append((b - 1 if b > bar else b, n))
        self.sections = sorted(dict(sections).items())
        keys = {}
        for b, k in self.key_changes:
            keys[b - 1 if b > bar else b] = k          # a change on the deleted bar moves to the next one
        self.key_changes = sorted((b, k) for b, k in keys.items() if 0 < b < len(self.meters) - 1)
        del self.meters[bar]

    def _split_at(self, voice: str, time: Fraction) -> None:
        result = []
        for note in self.voices[voice]:
            if note.onset < time < note.end:
                result += [Note(note.onset, note.pitch, time - note.onset), Note(time, note.pitch, note.end - time)]
            else:
                result.append(note)
        self.voices[voice] = result

    # --- whole-score changes ---------------------------------------------
    def transpose(self, semitones: int) -> None:
        if not semitones:
            return
        for voice in VOICES:
            for note in self.voices[voice]:
                if not 0 <= note.pitch + semitones <= 127:
                    raise ValueError("Transposing would move notes out of range")
        self.key = theory.transpose_key(self.key, semitones)
        self.key_changes = [(b, theory.transpose_key(k, semitones)) for b, k in self.key_changes]
        for voice in VOICES:
            for note in self.voices[voice]:
                note.pitch += semitones
        self.chords = [(t, theory.transpose_chord(c, semitones, self.key_at(self.bar_of(t))))
                       for t, c in self.chords]

    def without_chords(self) -> "Score":
        clone = self.copy()
        clone.chords = []
        return clone


class History:
    """Undo/redo by snapshots - scores are small, and snapshots cannot go wrong."""

    def __init__(self, score: Score, limit: int = 100):
        self.score = score
        self._undo: list[Score] = []
        self._redo: list[Score] = []
        self.limit = limit

    def checkpoint(self) -> None:
        self._undo.append(self.score.copy())
        del self._undo[:-self.limit]
        self._redo.clear()

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self.score)
        self.score = self._undo.pop()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self.score)
        self.score = self._redo.pop()
        return True

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)
