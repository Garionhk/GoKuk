from app import music


def test_compose_matches_yue2_example_shape():
    style = music.Style(language="English", genres=["piano pop"], moods=["warm"],
                        vocal="expressive female voice", instruments=["acoustic piano", "light drums"], bpm=88)
    assert style.compose() == ("English, warm piano pop, expressive female voice, acoustic piano, "
                               "light drums, 88 BPM")


def test_compose_dedupes_and_handles_auto_tempo_and_extras():
    style = music.Style(language="Cantonese", genres=["Canto-pop", "rock"], moods=[], vocal="",
                        instruments=["strings"], bpm=None, extra="big chorus, Strings,  ")
    assert style.compose() == "Cantonese, Canto-pop, rock, strings, big chorus"


def test_style_round_trip():
    style = music.Style(language="Japanese", genres=["J-pop"], bpm=120, extra="x")
    assert music.Style.from_dict(style.to_dict()) == style


def test_lyrics_hints():
    assert music.check_lyrics("")[0].level == "warn"
    notes = music.check_lyrics("hello\nworld")
    assert any("section tags" in n.text for n in notes)
    empty = music.check_lyrics("[Verse]\n[Chorus]\nla la")
    assert [n.name for n in empty if "empty" in n.text] == ["Verse"]
    assert music.check_lyrics(music.SAMPLE_LYRICS["en"]) == []
    assert music.check_lyrics(music.SAMPLE_LYRICS["zh-Hant"]) == []


def test_sections():
    assert music.sections("[Verse]\na\n [Chorus] \nb") == ["Verse", "Chorus"]
