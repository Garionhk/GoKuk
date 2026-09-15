"""Names for pitches, keys and chords, and moving them to another key.

Everything is spelled for the key it sits in: the black key above F is F♯ in
E major but G♭ in D♭ major. The rule used throughout: a pitch in the key's
scale takes the scale's letter;
any other pitch is written sharp in sharp keys (and C major / A minor) and flat
in flat keys. That matches what the native ABC writer produces.
"""
from __future__ import annotations

import re

from app.score.abc_dialect import KEYS, NATURAL, QUALITIES

LETTERS = "CDEFGAB"
SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FLAT_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]

#: Keys offered in the editor, in circle order, one spelling each (≤6 accidentals).
MAJOR_KEYS = ["C", "Db", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
MINOR_KEYS = ["Cm", "C#m", "Dm", "Ebm", "Em", "Fm", "F#m", "Gm", "G#m", "Am", "Bbm", "Bm"]

QUALITY_INTERVALS = {
    "": (0, 4, 7), "m": (0, 3, 7), "dim": (0, 3, 6), "aug": (0, 4, 8), "7": (0, 4, 7, 10),
    "maj7": (0, 4, 7, 11), "m7": (0, 3, 7, 10), "dim7": (0, 3, 6, 9), "m7b5": (0, 3, 6, 10),
    "sus4": (0, 5, 7), "sus2": (0, 2, 7), "6": (0, 4, 7, 9), "m6": (0, 3, 7, 9),
    "7sus4": (0, 5, 7, 10), "m(maj7)": (0, 3, 7, 11),
}
assert set(QUALITY_INTERVALS) == set(QUALITIES)

_NAME = re.compile(r"([A-G])(bb|##|b|#)?")
_CHORD = re.compile(r"(?P<root>[A-G](?:bb|##|b|#)?)(?P<quality>.*?)(?:/(?P<bass>[A-G](?:bb|##|b|#)?))?$")
ACCIDENTAL_VALUE = {"": 0, "#": 1, "##": 2, "b": -1, "bb": -2}


def pitch_class(name: str) -> int:
    match = _NAME.fullmatch(name)
    if not match:
        raise ValueError(f"Not a note name: {name!r}")
    return (NATURAL[match.group(1)] + ACCIDENTAL_VALUE[match.group(2) or ""]) % 12


def is_minor(key: str) -> bool:
    return key.endswith("m")


def tonic(key: str) -> str:
    return key[:-1] if is_minor(key) else key


def uses_flats(key: str) -> bool:
    return KEYS.get(key, 0) < 0


def key_signature(key: str) -> dict[str, int]:
    """Letter -> -1/0/+1 for the key's signature."""
    count = KEYS[key]
    result = {letter: 0 for letter in LETTERS}
    for letter in ("FCGDAEB" if count > 0 else "BEADGCF")[:abs(count)]:
        result[letter] = 1 if count > 0 else -1
    return result


def spell(pitch: int, key: str) -> tuple[str, int, int]:
    """(letter, alteration, octave) for a MIDI pitch in ``key``.

    Octave follows scientific pitch: MIDI 60 is C4. The octave belongs to the
    *letter*, so C♭5 (MIDI 71) is letter C, octave 5, alteration -1.
    """
    signature = key_signature(key)
    pc = pitch % 12
    for letter in LETTERS:                       # in the scale?
        if (NATURAL[letter] + signature[letter]) % 12 == pc:
            alteration = signature[letter]
            break
    else:
        # Outside the scale: the key's own accidental first, then a natural (E in
        # F# major, whose signature makes every E sharp), then the other accidental.
        preferred = -1 if uses_flats(key) else 1
        for alteration in (preferred, 0, -preferred):
            letter = next((l for l in LETTERS if (NATURAL[l] + alteration) % 12 == pc), None)
            if letter is not None:
                break
    written = pitch - alteration
    octave = written // 12 - 1
    return letter, alteration, octave


def note_name(pitch: int, key: str = "C", octave: bool = True) -> str:
    letter, alteration, oct_ = spell(pitch, key)
    mark = {-2: "𝄫", -1: "♭", 0: "", 1: "♯", 2: "𝄪"}[alteration]
    return f"{letter}{mark}{oct_}" if octave else f"{letter}{mark}"


def name_for_pitch_class(pc: int, key: str) -> str:
    """A chord root/bass name spelled for the key (ASCII: C#, Bb)."""
    letter, alteration, _ = spell(60 + pc % 12, key)
    return letter + {-2: "bb", -1: "b", 0: "", 1: "#", 2: "##"}[alteration]


def parse_chord(symbol: str) -> tuple[int, str, int | None]:
    """'F#m7/C#' -> (root pitch class, quality, bass pitch class or None)."""
    match = _CHORD.fullmatch(symbol)
    if not match or match.group("quality") not in QUALITY_INTERVALS:
        raise ValueError(f"Unsupported chord {symbol!r}")
    bass = match.group("bass")
    return pitch_class(match.group("root")), match.group("quality"), pitch_class(bass) if bass else None


def chord_pitches(symbol: str, around: int = 48) -> list[int]:
    """MIDI pitches for a preview voicing, root near ``around``."""
    root, quality, bass = parse_chord(symbol)
    base = around + (root - around) % 12
    notes = [base + i for i in QUALITY_INTERVALS[quality]]
    if bass is not None:
        notes.insert(0, around - 12 + (bass - around) % 12)
    return notes


def transpose_key(key: str, semitones: int) -> str:
    pool = MINOR_KEYS if is_minor(key) else MAJOR_KEYS
    pc = (pitch_class(tonic(key)) + semitones) % 12
    for candidate in pool:
        if pitch_class(tonic(candidate)) == pc:
            return candidate
    raise ValueError(key)


def transpose_chord(symbol: str, semitones: int, new_key: str) -> str:
    root, quality, bass = parse_chord(symbol)
    text = name_for_pitch_class(root + semitones, new_key) + quality
    if bass is not None:
        text += "/" + name_for_pitch_class(bass + semitones, new_key)
    return text


def nearest_interval(from_key: str, to_key: str) -> int:
    """Semitones from one key to another, choosing the smaller move (-6..+6)."""
    diff = (pitch_class(tonic(to_key)) - pitch_class(tonic(from_key))) % 12
    return diff - 12 if diff > 6 else diff


def key_label(key: str) -> str:
    name = tonic(key).replace("#", "♯").replace("b", "♭")
    return f"{name} minor" if is_minor(key) else f"{name} major"
