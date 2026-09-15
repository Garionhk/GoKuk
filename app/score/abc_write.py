"""Write a Score back as the native two-voice ABC that YuE2 reads.

The layout copies YuE2's own exporter: the fixed eight-line header, L:1/32, one
to four bars per group, a new group wherever a section starts (with its
``% name`` comment), a ``V: Vocal`` line carrying the chord symbols and a
``V: Ins`` line, and ``Z`` for bars that are completely silent.

Inside a bar the writer walks the time line and cuts at every note start, note
end and chord change. A note that crosses a barline or a chord change is
written as tied pieces (``"C"E16-"Am7"E16``), and every length is broken into
the durations the format allows (1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48).

Pitch spelling follows ``theory.spell``. Accidentals are the subtle part: they
last to the end of the bar and apply to the same letter in every octave. The
writer keeps exactly that state per bar and writes an accidental whenever the
state would give the wrong pitch, including on tied continuations.

Last, the text is parsed again with the vendored YuE2 parser and compared with
the Score - notes, chords, bars, sections, key, tempo. If anything differs the
writer raises instead of returning a score that means something else.
"""
from __future__ import annotations

from fractions import Fraction

from app.score import abc_dialect as dialect
from app.score import theory
from app.score.model import VOICES, Note, Score

UNIT = Fraction(1, 32)                      # L:1/32
UNITS_PER_QUARTER = 8
PIECES = (48, 32, 24, 16, 12, 8, 6, 4, 3, 2, 1)
ACCIDENTAL = {-2: "__", -1: "_", 0: "=", 1: "^", 2: "^^"}
HEADER_VOICES = ['V: Vocal clef=treble name="Vocal Melody" snm="Vocal"',
                 'V: Ins clef=treble name="Ins Melody" snm="Inst."']


class WriteError(ValueError):
    pass


def units(time: Fraction) -> int:
    value = Fraction(time) * UNITS_PER_QUARTER
    if value.denominator != 1:
        raise WriteError(f"Timing {time} is finer than a 32nd note")
    return int(value)


def split_units(total: int) -> list[int]:
    pieces = []
    while total > 0:
        piece = next(p for p in PIECES if p <= total)
        pieces.append(piece)
        total -= piece
    return pieces


def note_token(letter: str, octave: int) -> str:
    if octave >= 5:
        return letter.lower() + "'" * (octave - 5)
    return letter + "," * (4 - octave)


def _check(score: Score) -> None:
    for voice in VOICES:
        last_end = Fraction(0)
        for note in sorted(score.voices[voice], key=lambda n: n.onset):
            if note.onset < last_end:
                raise WriteError(f"{voice}: overlapping notes at {note.onset}")
            if note.end > score.length or note.duration <= 0:
                raise WriteError(f"{voice}: note outside the score at {note.onset}")
            units(note.onset), units(note.duration)
            last_end = note.end
    for onset, _ in score.chords:
        units(onset)
        if not 0 <= onset < score.length:
            raise WriteError(f"Chord outside the score at {onset}")
    for key in [score.key] + [k for _, k in score.key_changes]:
        if key not in dialect.KEYS:
            raise WriteError(f"Unsupported key {key}")


def _bar_text(score: Score, voice: str, bar: int, chords: list[tuple[Fraction, str]]) -> str:
    start, end = score.bar_start(bar), score.bar_start(bar + 1)
    notes = [n for n in score.voices[voice] if n.onset < end and n.end > start]
    bar_chords = [(t, c) for t, c in chords if start <= t < end] if voice == "Vocal" else []
    if not notes and not bar_chords:
        return "Z"
    cuts = {start, end}
    for note in notes:
        cuts.update(t for t in (note.onset, note.end) if start < t < end)
    cuts.update(t for t, _ in bar_chords)
    points = sorted(cuts)
    chord_at = dict(bar_chords)
    key = score.key_at(bar)
    signature = theory.key_signature(key)
    state = dict(signature)                 # letter -> alteration in force this bar
    out = []
    for a, b in zip(points, points[1:]):
        if a in chord_at:
            out.append(f'"{chord_at[a]}"')
        note = next((n for n in notes if n.onset <= a < n.end), None)
        pieces = split_units(units(b - a))
        if note is None:
            out.extend(f"z{p}" for p in pieces)
            continue
        letter, alteration, octave = theory.spell(note.pitch, key)
        for index, piece in enumerate(pieces):
            accidental = ""
            if state[letter] != alteration:
                accidental = ACCIDENTAL[alteration]
                state[letter] = alteration
            piece_end = b if index == len(pieces) - 1 else None
            continues = index < len(pieces) - 1 or (piece_end is not None and piece_end < note.end)
            out.append(f"{accidental}{note_token(letter, octave)}{piece}{'-' if continues else ''}")
    return "".join(out)


def _groups(score: Score) -> list[tuple[list[str], list[int]]]:
    """[(section names starting here, bar indexes)] - at most four bars each."""
    starts = {}
    for bar, name in sorted(score.sections):
        if 0 <= bar < score.bars:
            starts.setdefault(bar, []).append(name)
    groups, current = [], []
    for bar in range(score.bars):
        metre_change = bar > 0 and score.meters[bar] != score.meters[bar - 1]
        key_change = bar > 0 and score.key_at(bar) != score.key_at(bar - 1)
        if current and (bar in starts or len(current) == 4 or metre_change or key_change):
            groups.append(current)
            current = []
        current.append(bar)
    if current:
        groups.append(current)
    return [(starts.get(g[0], []), g) for g in groups]


def _compress(bars: list[str]) -> str:
    """Join bar texts, folding runs of whole-bar rests into Z2..Z4."""
    parts, run = [], 0
    for text in bars + [None]:
        if text == "Z":
            run += 1
            continue
        if run:
            parts.append("Z" if run == 1 else f"Z{run}")
            run = 0
        if text is not None:
            parts.append(text)
    return "|".join(parts) + "|"


def to_abc(score: Score, strip_chords: bool = False) -> str:
    _check(score)
    chords = [] if strip_chords else sorted(score.chords)
    n, d = score.meter
    lines = ["X:1", "T:", f"M:{n}/{d}", "L:1/32", f"Q:1/4={int(score.bpm)}", *HEADER_VOICES, f"K:{score.key}"]
    current_meter, current_key = score.meter, score.key
    for names, bars in _groups(score):
        lines += [f"% {name}" for name in names]
        meter, key = score.meters[bars[0]], score.key_at(bars[0])
        for voice in VOICES:
            lines.append(f"V: {voice}")
            if meter != current_meter:
                lines.append(f"M:{meter[0]}/{meter[1]}")     # metre and key changes start their own group
            if key != current_key:
                lines.append(f"K:{key}")
            lines.append(_compress([_bar_text(score, voice, bar, chords) for bar in bars]))
        current_meter, current_key = meter, key
    text = "\n".join(lines) + "\n"
    verify(score, text, strip_chords)
    return text


def verify(score: Score, text: str, strip_chords: bool = False) -> None:
    """Parse ``text`` and insist it means exactly ``score``."""
    try:
        parsed = dialect.parse(text)
    except dialect.AbcError as error:
        raise WriteError(f"The written score did not validate: {error}") from error
    problems = []
    if parsed.bpm != int(score.bpm):
        problems.append("tempo")
    vocal = parsed.voices["Vocal"]
    if [tuple(bar[2]) for bar in vocal.bars] != [tuple(m) for m in score.meters]:
        problems.append("bars and metres")
    expected_keys = [(Fraction(0), score.key)] + [(score.bar_start(b), k) for b, k in sorted(score.key_changes)]
    if [(Fraction(t), k) for t, k in vocal.keys] != expected_keys:
        problems.append("keys")
    for voice in VOICES:
        expected = [[n.onset, n.pitch, n.duration] for n in sorted(score.voices[voice], key=lambda n: n.onset)]
        if parsed.voices[voice].notes != expected:
            problems.append(f"{voice} notes")
    expected_chords = [] if strip_chords else sorted(score.chords)
    if [(t, c) for t, c in vocal.chords] != expected_chords:
        problems.append("chords")
    expected_sections = sorted((b, n) for b, n in score.sections if 0 <= b < score.bars)
    if parsed.sections != expected_sections:
        problems.append("sections")
    if problems:
        raise WriteError("The written score would not match what you edited: " + ", ".join(problems))
