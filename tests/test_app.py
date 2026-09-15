import json
from pathlib import Path

from app import gpu, i18n, paths
from app.config import Config
from app.jobs import requests as rq
from app.songs import Library, Song, slug, title_from_lyrics

ROOT = Path(__file__).resolve().parent.parent


def test_paths_follow_root_override(tmp_path, monkeypatch):
    monkeypatch.setenv("GOKUK_ROOT", str(tmp_path))
    assert paths.root() == tmp_path.resolve()
    assert paths.yue2_python() == tmp_path.resolve() / "runtime" / "yue2" / "python.exe"
    assert paths.settings_file().parent == tmp_path.resolve()


def test_song_job_uses_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("GOKUK_ROOT", str(tmp_path))
    cfg = Config(tmp_path / "settings.json")
    cfg.set("performance", "16")                       # a setting saved by an older version
    job = rq.song_job(cfg, style="s", lyrics="l", cot="melody", seed=5, output_dir=tmp_path / "o", abc="X:1")
    assert job["memory"] == "balanced" and "budget_gib" not in job
    cfg.set("performance", "low")
    assert rq.song_job(cfg, style="s", lyrics="l", cot="full", seed=1, output_dir=tmp_path)["memory"] == "low"
    assert job["cot"] == "melody" and job["abc"] == "X:1"
    assert job["model_dir"].endswith("YuE2-3B")


def test_worker_versions_match_the_window():
    from app.jobs.worker_proc import EXPECTED_WORKER_VERSION, worker_version

    for name in ("yue2", "sheetsage"):
        assert worker_version(ROOT / "workers" / f"{name}_worker.py") == EXPECTED_WORKER_VERSION


def test_gpu_profiles_and_verdict():
    assert gpu.auto_memory(gpu.GPU("A4000", 16376, "581.57")) == "low"
    assert gpu.auto_memory(gpu.GPU("4090", 24564, "581.57")) == "fast"
    assert gpu.auto_memory(gpu.GPU("3060", 12288, "581.57")) == "low"
    assert gpu.verdict(None)[0] == "bad"
    assert gpu.verdict(gpu.GPU("old", 24564, "531.10"))[0] == "bad"
    assert gpu.verdict(gpu.GPU("A4000", 16376, "581.57"))[0] == "warn"


def test_library_round_trip_and_trash(tmp_path):
    lib = Library(tmp_path / "songs")
    folder = lib.new_folder("晚風 Night / Wind?")
    folder.mkdir()
    song = Song(folder, title="Night", style="pop", lyrics="[Verse]\nhi", seed=3, created="2026-09-12T10:00:00")
    song.extra["style_builder"] = {"language": "English"}
    song.save()
    (folder / "peaks.json").write_text(json.dumps({"seconds": 61.5, "peaks": [0.1, 0.5]}))
    loaded = lib.songs()[0]
    assert loaded.title == "Night" and loaded.seconds == 61.5 and loaded.peaks() == [0.1, 0.5]
    assert loaded.extra["style_builder"] == {"language": "English"}
    lib.trash(loaded)
    assert lib.songs() == [] and (tmp_path / "songs" / "_trash" / folder.name).is_dir()


def test_titles_and_slugs():
    assert title_from_lyrics("[Verse]\n\nNeon fades\nx") == "Neon fades"
    assert slug("Hello, World!") == "hello-world"
    assert slug("???") == "song"


def test_catalogue_covers_every_string():
    import extract_strings

    catalogue = json.loads((ROOT / "lang" / "zh-Hant.json").read_text(encoding="utf-8"))
    missing = [s for s in extract_strings.strings() if not catalogue.get(s)]
    assert missing == []
    # Placeholders must survive translation.
    import re
    for english, chinese in catalogue.items():
        if english.startswith("_"):
            continue
        assert set(re.findall(r"{\w+}", english)) == set(re.findall(r"{\w+}", chinese)), english


def test_i18n_falls_back_to_english():
    i18n.load("zh-Hant")
    try:
        assert i18n.t("Create") == "創作"
        assert i18n.t("A sentence nobody translated") == "A sentence nobody translated"
        assert i18n.t("seed {n}", n=4) == "種子 4"
    finally:
        i18n.load("en")


def test_song_settings_come_from_the_run(tmp_path):
    folder = tmp_path / "song"
    folder.mkdir()
    song = Song(folder, title="Night", kind="cover", style="shown style", lyrics="shown", seed=1,
                source_audio="orig.mp3", extra={"style_builder": {"language": "English"},
                                                 "source_path": "D:/music/orig.mp3"})
    song.save()
    (folder / "job.json").write_text(json.dumps({"style": "sent style", "lyrics": "[Verse]\nsent", "cot": "melody",
                                                 "seed": 42, "cfg_scale": 1.5, "abc": "chord-free"}),
                                     encoding="utf-8")
    settings = Song.load(folder).settings()
    assert (settings.style, settings.lyrics, settings.cot, settings.seed, settings.cfg_scale) == \
           ("sent style", "[Verse]\nsent", "melody", 42, 1.5)
    assert settings.score == "chord-free" and settings.source_path == "D:/music/orig.mp3"
    (folder / Song.INPUT_SCORE).write_text("full score with chords", encoding="utf-8")
    assert Song.load(folder).settings().score == "full score with chords"      # preferred over job.json
