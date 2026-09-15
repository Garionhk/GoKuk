"""Words YuE2 understands, and the small rules around lyrics and style.

YuE2 is steered by a plain comma-separated style line, as in its own example:

    English, warm piano pop, expressive female voice, acoustic piano,
    rounded bass and light drums, lyrical memorable melody, 88 BPM

The style builder offers the common pieces as chips so nobody has to guess the
vocabulary, and always shows the line it produces so people learn it. The
English words go to the model; the interface shows them translated.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.i18n import N

#: (value sent to the model, label shown) - labels go through t() when shown.
LANGUAGES = [
    ("English", N("English")),
    ("Cantonese", N("Cantonese")),
    ("Mandarin", N("Mandarin")),
    ("Japanese", N("Japanese")),
    ("Korean", N("Korean")),
    ("Spanish", N("Spanish")),
]

GENRES = [
    ("pop", N("Pop")), ("piano pop", N("Piano pop")), ("rock", N("Rock")),
    ("rock ballad", N("Rock ballad")), ("R&B", N("R&B")), ("hip hop", N("Hip hop")),
    ("jazz", N("Jazz")), ("jazz-funk", N("Jazz-funk")), ("folk", N("Folk")),
    ("country", N("Country")), ("electronic dance", N("EDM")), ("synthwave", N("Synthwave")),
    ("lo-fi", N("Lo-fi")), ("city pop", N("City pop")), ("metal", N("Metal")),
    ("soul", N("Soul")), ("reggae", N("Reggae")), ("bossa nova", N("Bossa nova")),
    ("cinematic orchestral", N("Orchestral")), ("Canto-pop", N("Canto-pop")),
    ("Mandopop", N("Mandopop")), ("J-pop", N("J-pop")), ("K-pop", N("K-pop")),
]

MOODS = [
    ("warm", N("Warm")), ("uplifting", N("Uplifting")), ("melancholic", N("Melancholic")),
    ("romantic", N("Romantic")), ("energetic", N("Energetic")), ("dreamy", N("Dreamy")),
    ("dark", N("Dark")), ("playful", N("Playful")), ("epic", N("Epic")),
    ("nostalgic", N("Nostalgic")), ("calm", N("Calm")), ("emotional", N("Emotional")),
]

VOCALS = [
    ("expressive female voice", N("Female")),
    ("warm male vocal", N("Male")),
    ("powerful female vocal", N("Powerful female")),
    ("powerful male vocal", N("Powerful male")),
    ("soft breathy female vocal", N("Breathy female")),
    ("raspy male vocal", N("Raspy male")),
    ("male and female duet", N("Duet")),
    ("choir", N("Choir")),
]

INSTRUMENTS = [
    ("acoustic piano", N("Piano")), ("Rhodes", N("Rhodes")), ("acoustic guitar", N("Acoustic guitar")),
    ("electric guitar", N("Electric guitar")), ("bass", N("Bass")), ("drums", N("Drums")),
    ("light drums", N("Light drums")), ("strings", N("Strings")), ("synth pads", N("Synth pads")),
    ("808 bass", N("808")), ("brass", N("Brass")), ("saxophone", N("Saxophone")),
    ("violin", N("Violin")), ("erhu", N("Erhu")), ("guzheng", N("Guzheng")),
]

SECTIONS = ["Intro", "Verse", "Pre-Chorus", "Chorus", "Bridge", "Outro"]

BPM_MIN, BPM_MAX, BPM_DEFAULT = 50, 180, 90

SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")


@dataclass
class Style:
    """The builder's state. ``compose()`` turns it into the line YuE2 reads."""
    language: str = "English"
    genres: list[str] = field(default_factory=lambda: ["pop"])
    moods: list[str] = field(default_factory=list)
    vocal: str = "expressive female voice"
    instruments: list[str] = field(default_factory=list)
    bpm: int | None = BPM_DEFAULT
    extra: str = ""

    def compose(self) -> str:
        parts: list[str] = []
        if self.language:
            parts.append(self.language)
        # The first mood leads the first genre, as in YuE2's "warm piano pop".
        lead = " ".join(p for p in (self.moods[:1] + self.genres[:1]) if p)
        if lead:
            parts.append(lead)
        parts += self.genres[1:] + self.moods[1:]
        if self.vocal:
            parts.append(self.vocal)
        parts += self.instruments
        extra = [p.strip() for p in self.extra.split(",") if p.strip()]
        parts += extra
        if self.bpm:
            parts.append(f"{int(self.bpm)} BPM")
        seen, unique = set(), []
        for part in parts:
            key = part.lower()
            if key not in seen:
                seen.add(key)
                unique.append(part)
        return ", ".join(unique)

    def to_dict(self) -> dict:
        return {"language": self.language, "genres": self.genres, "moods": self.moods,
                "vocal": self.vocal, "instruments": self.instruments, "bpm": self.bpm,
                "extra": self.extra}

    @classmethod
    def from_dict(cls, data: dict | None) -> "Style":
        style = cls()
        for key, value in (data or {}).items():
            if hasattr(style, key):
                setattr(style, key, value)
        return style


@dataclass
class LyricNote:
    level: str       # "warn" | "info"
    text: str        # English; translated when shown
    line: int | None = None
    name: str = ""


def sections(lyrics: str) -> list[str]:
    return [m.group(1).strip() for line in lyrics.splitlines() if (m := SECTION_RE.match(line))]


def check_lyrics(lyrics: str) -> list[LyricNote]:
    """Plain-language hints; none of them block a run."""
    notes: list[LyricNote] = []
    lines = lyrics.splitlines()
    if not lyrics.strip():
        return [LyricNote("warn", N("Write some lyrics first - or press Sample."))]
    if not sections(lyrics):
        notes.append(LyricNote("warn", N("Add section tags like [Verse] and [Chorus] - "
                                         "they tell the singer how the song is built.")))
    current, count = None, 0
    for index, line in enumerate(lines + ["[End]"], start=1):
        if SECTION_RE.match(line):
            if current is not None and count == 0:
                notes.append(LyricNote("warn", N("An empty section: [{name}]"), index, current))
            current, count = SECTION_RE.match(line).group(1), 0
        elif line.strip():
            count += 1
            if len(line) > 60 and not re.search(r"[぀-ヿ㐀-鿿가-힯]", line):
                notes.append(LyricNote("info", N("Line {line} is long - shorter lines are easier to sing."),
                                       index))
            elif len(line) > 24 and re.search(r"[㐀-鿿]", line):
                notes.append(LyricNote("info", N("Line {line} is long - shorter lines are easier to sing."),
                                       index))
    words = sum(len(l.split()) for l in lines if l.strip() and not SECTION_RE.match(l))
    if words > 450:
        notes.append(LyricNote("info", N("Very long lyrics - the song may be cut off at the end.")))
    return notes


SAMPLE_LYRICS = {
    "en": "[Verse]\nNeon fades along the lane\nFootsteps keep the time of rain\n"
          "Fold the night and leave it here\nMorning has a sky to clear\n\n"
          "[Chorus]\nLet the day come into view\nEvery road begins with you\n"
          "Hold a little room for light\nWe will sing beyond the night",
    "zh-Hant": "[Verse]\n晚風輕輕吹過窗前\n你留下的笑還在昨天\n街燈一盞一盞地亮\n我在雨裡把你想\n\n"
               "[Chorus]\n讓這首歌陪你走遠\n把所有想念唱成明天\n就算夜再長再冷\n我也會為你點燈",
}
