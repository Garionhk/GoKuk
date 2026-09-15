import io
import wave
from fractions import Fraction as F
from pathlib import Path

import pytest

from app.score import abc_dialect, preview, theory
from app.score.abc_write import WriteError, to_abc
from app.score.model import History, Score, UnsupportedScore

FIXTURES = sorted((Path(__file__).parent / "fixtures" / "scores").glob("*.abc"))

HEADER = ('X:1\nT:\nM:4/4\nL:1/32\nQ:1/4=90\nV: Vocal clef=treble name="Vocal Melody" snm="Vocal"\n'
          'V: Ins clef=treble name="Ins Melody" snm="Inst."\n')


def notes(score, voice="Vocal"):
    return [(n.onset, n.pitch, n.duration) for n in score.voices[voice]]


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_round_trip_is_exact(path):
    original = abc_dialect.parse(path.read_text(encoding="utf-8"))
    score = Score.from_abc(path.read_text(encoding="utf-8"))
    written = abc_dialect.parse(to_abc(score))
    for voice in ("Vocal", "Ins"):
        assert written.voices[voice].notes == original.voices[voice].notes
    assert written.voices["Vocal"].chords == original.voices["Vocal"].chords
    assert len(written.voices["Vocal"].bars) == len(original.voices["Vocal"].bars)
    assert written.sections == original.sections and written.bpm == original.bpm


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
@pytest.mark.parametrize("step", [1, 2, 5, -3, -6])
def test_transpose_moves_every_pitch_and_back(path, step):
    text = path.read_text(encoding="utf-8")
    score = Score.from_abc(text)
    moved = Score.from_abc(text)
    moved.transpose(step)
    reread = Score.from_abc(to_abc(moved))                       # the transposed text is valid
    for voice in ("Vocal", "Ins"):
        assert [p for _, p, _ in notes(reread, voice)] == [p + step for _, p, _ in notes(score, voice)]
    reread.transpose(-step)
    assert to_abc(reread) == to_abc(score)


def test_key_and_chord_names_follow_the_key():
    assert theory.transpose_key("C", 2) == "D"
    assert theory.transpose_key("Am", 2) == "Bm"
    assert theory.transpose_key("C", -2) == "Bb"
    assert theory.transpose_chord("F/A", 2, "G") == "G/B"
    assert theory.transpose_chord("Am7", 1, "Bb") == "Bbm7"
    assert theory.transpose_chord("E7sus4", 3, "G") == "G7sus4"
    assert theory.note_name(66, "D") == "F♯4" and theory.note_name(66, "Db") == "G♭4"
    assert theory.nearest_interval("C", "Bb") == -2 and theory.nearest_interval("C", "F#") == 6


def test_accidentals_carry_across_octaves_and_ties():
    # In C: F#4 held over the barline, then F natural - the case the dialect notes call out.
    score = Score.blank(bars=2, key="C", sections=[(0, "verse")])
    score.add_note("Vocal", F(0), 66, F(5))            # F#4 for five quarters
    score.add_note("Vocal", F(5), 65, F(3))            # F4
    text = to_abc(score)
    parsed = abc_dialect.parse(HEADER + "K:C\n" + text.split("K:C\n", 1)[1])
    assert parsed.voices["Vocal"].notes == [[F(0), 66, F(5)], [F(5), 65, F(3)]]
    # A sharp in one octave makes the same letter sharp an octave up; the writer must cancel it.
    score = Score.blank(bars=1, key="C")
    score.add_note("Vocal", F(0), 66, F(1))            # F#4
    score.add_note("Vocal", F(1), 77, F(1))            # F5 natural
    assert "=f" in to_abc(score)
    assert notes(Score.from_abc(to_abc(score))) == [(F(0), 66, F(1)), (F(1), 77, F(1))]


def test_chord_inside_a_held_note_is_tied():
    score = Score.blank(bars=1, key="C")
    score.add_note("Vocal", F(0), 64, F(4))
    score.set_chord(F(0), "C")
    score.set_chord(F(2), "Am7")
    text = to_abc(score)
    assert '"C"E16-"Am7"E16|' in text
    assert notes(Score.from_abc(text)) == [(F(0), 64, F(4))]


def test_monophonic_editing_trims_neighbours():
    score = Score.blank(bars=2)
    a = score.add_note("Vocal", F(0), 60, F(4))
    score.add_note("Vocal", F(1), 64, F(1))            # lands inside the first note
    assert notes(score) == [(F(0), 60, F(1)), (F(1), 64, F(1)), (F(2), 60, F(2))]
    assert a not in score.voices["Vocal"] or a.duration == F(1)
    score.add_bars(1, at=0)
    assert notes(score)[0][0] == F(4)
    score.delete_bar(0)
    assert notes(score)[0] == (F(0), 60, F(1))


def test_strip_chords_keeps_every_note():
    text = FIXTURES[2].read_text(encoding="utf-8")
    score = Score.from_abc(text)
    stripped = abc_dialect.parse(to_abc(score, strip_chords=True))
    assert stripped.voices["Vocal"].chords == []
    assert stripped.voices["Vocal"].notes == abc_dialect.parse(text).voices["Vocal"].notes


def test_writer_refuses_what_it_cannot_express():
    from app.score.model import Note

    score = Score.blank(bars=1)
    score.voices["Vocal"] = [Note(F(1, 3), 60, F(1, 3))]       # a triplet: finer than the grid
    with pytest.raises(WriteError):
        to_abc(score)
    with pytest.raises(ValueError):
        Score.blank(bars=1).set_chord(F(0), "Cmaj9")


def test_key_changes_open_in_the_editor_and_transpose():
    # A real SheetSage2 transcription: A major <-> F# major, one change written inside a bar.
    path = next(p for p in FIXTURES if p.name == "transcription-key-changes.abc")
    text = path.read_text(encoding="utf-8")
    score = Score.from_abc(text)
    assert score.key == "A" and score.key_changes
    assert notes(Score.from_abc(to_abc(score))) == notes(score)
    moved = score.copy()
    moved.transpose(-2)
    assert moved.key == "G" and all(k in ("G", "E") for _, k in moved.key_changes)
    assert [p for _, p, _ in notes(Score.from_abc(to_abc(moved)))] == [p - 2 for _, p, _ in notes(score)]


def test_metre_changes_survive_editing():
    path = next(p for p in FIXTURES if p.name == "plan-metre-change.abc")
    score = Score.from_abc(path.read_text(encoding="utf-8"))
    odd = [i for i, m in enumerate(score.meters) if m != (4, 4)]
    assert odd, "fixture should contain a bar in another metre"
    bar = odd[0]
    assert score.bar_length_at(bar) == F(9, 2)
    score.add_bars(1, at=bar)                       # insert before the 9/8 bar
    score.delete_bar(bar + 1)                       # and remove the 9/8 bar itself
    reread = Score.from_abc(to_abc(score))
    assert reread.meters == score.meters and (9, 8) not in reread.meters


def test_history_undo_redo():
    history = History(Score.blank(bars=1))
    history.checkpoint()
    history.score.add_note("Vocal", F(0), 60, F(1))
    assert history.undo() and notes(history.score) == []
    assert history.redo() and len(notes(history.score)) == 1


def test_preview_length_matches_tempo():
    score = Score.from_abc(FIXTURES[2].read_text(encoding="utf-8"))
    audio = wave.open(io.BytesIO(preview.render(score)))
    seconds = audio.getnframes() / audio.getframerate()
    assert abs(seconds - score.seconds) < 0.5


def test_every_pitch_spells_back_to_itself_in_every_key():
    # E natural in F# major once came out as C: the fallback spelling was wrong.
    for key in abc_dialect.KEYS:
        for pitch in range(36, 96):
            letter, alteration, octave = theory.spell(pitch, key)
            assert abc_dialect.NATURAL[letter] + alteration + 12 * (octave + 1) == pitch, (key, pitch)
