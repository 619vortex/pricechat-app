"""Short Market worker tests (no network, no server, no GPU). TEST FIXTURES only: a fake backend client, tiny
FFmpeg-made clips standing in for the video model, and beeps standing in for the voice."""

import glob
import subprocess
from pathlib import Path
from typing import Any

import pytest

from pricechat_worker import gpu, render, shorts, story, tts

JOB: dict[str, Any] = {
    "kind": "short", "job_id": 7, "generation": 1, "category": "markets", "topic": "Crypto market mood today: Fear (31/100)",
    "angle": "A market story.", "seconds": 30, "quality": "fast", "voice": "am_michael",
    "style": {"key": "clay", "label": "Claymation", "accent": "#ff8a5c", "prompt": "handmade claymation stop-motion look"},
    "brief": {"headline": "The crypto market's mood today is Fear: 31 out of 100",
              "facts": [{"text": "The whole crypto market is worth $3.41 trillion.", "source": "CoinGecko"},
                        {"text": "The Crypto Fear & Greed Index reads 31 out of 100 today: Fear.", "source": "alternative.me"},
                        {"text": "Yesterday the index read 52: Neutral.", "source": "alternative.me"}],
              "sources": [{"name": "CoinGecko", "url": "https://www.coingecko.com"}]},
    "brand": {"name": "Price Chat", "language": "English", "personality": "", "audience": ""},
}


def test_template_story_tells_only_the_brief_and_every_scene_has_a_picture() -> None:
    s = story.finish(story.write(JOB, [], 4), JOB)
    assert s["engine"] == "template" and 4 <= len(s["segments"]) <= 12
    assert "$3.41 trillion" in s["voiceover"] and "31 out of 100" in s["voiceover"] and s["voiceover"].endswith("Follow for more.")
    assert all(len(g["visual"].split()) >= 4 and g["screen"] for g in s["segments"])
    assert s["sources"][0]["name"] == "CoinGecko" and len(s["storyboard"]) == len(s["segments"])


def test_story_prompt_asks_for_the_right_number_of_scenes_and_forbids_inventing() -> None:
    assert [story.scenes_for(n) for n in (20, 30, 40)] == [5, 7, 9]
    system, user = story._prompt(JOB, ["Scene 3 has no picture"])
    assert "Exactly 7 scenes" in system and "ONLY the facts in the brief" in system and "NEVER real people" in system
    assert "$3.41 trillion" in user and "Scene 3 has no picture" in user
    assert story.guide(JOB) in system and story.guide({**JOB, "generation": 2}) != story.guide(JOB)  # a remake gets another guide
    parsed = story._parse('noise {"segments": [{"text": "Hi.", "screen": "HI", "visual": "a robot waves"}, {"text": ""}], '
                          '"title": "T", "description": "D", "hashtags": ["#a"]} noise')
    assert parsed["segments"] == [{"text": "Hi.", "screen": "HI", "visual": "a robot waves"}]
    done = story.finish({**parsed, "segments": [dict(parsed["segments"][0])]}, JOB)
    assert done["voiceover"] == "Hi. Follow for more." and done["character"] == story.guide(JOB)


def test_shot_prompt_carries_the_style_and_the_guide_once() -> None:
    script = {"character": "a small round orange robot with big curious eyes"}
    p = story.shot_prompt({"visual": "golden coins pour into a glass vault"}, script, JOB["style"])
    assert p.startswith("Handmade claymation stop-motion look. golden coins pour into a glass vault. The guide of the story is a small round orange robot")
    assert "Rendered in 3D" in p and p.endswith("no text.")
    again = story.shot_prompt({"visual": "a small round orange robot with big curious eyes opens a vault"}, script, JOB["style"])
    assert "The guide of the story" not in again


def test_scene_length_becomes_a_frame_count_the_model_accepts() -> None:
    for seconds in (0.5, 2.0, 3.37, 4.3, 5.0, 9.0):
        n = gpu.frames_for(seconds)
        assert (n - 1) % 4 == 0 and gpu.MIN_FRAMES <= n <= gpu.MAX_FRAMES
    assert gpu.frames_for(4.0) == 97 and gpu.frames_for(9.0) == 121
    assert all(w % 32 == 0 and h % 32 == 0 for w, h, _ in gpu.QUALITY.values())


def _font() -> str:
    return next(iter(glob.glob("C:/Windows/Fonts/arialbd.ttf") + glob.glob("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")))


def test_titles_show_each_scenes_words_for_as_long_as_it_is_spoken(tmp_path: Path) -> None:
    segs = [{"text": "The market lost its nerve.", "screen": "Mood: fear"}, {"text": "It reads 31 today.", "screen": ""}]
    render.captions(segs, [(0.0, 2.0), (2.14, 4.0)], (255, 138, 92), _font(), tmp_path / "c.ass", titles=True)
    lines = (tmp_path / "c.ass").read_text(encoding="utf-8").splitlines()
    titles = [ln for ln in lines if ln.startswith("Dialogue: 1,")]
    assert len(titles) == 1 and titles[0].startswith("Dialogue: 1,0:00:00.00,0:00:02.10,Title") and titles[0].endswith("MOOD: FEAR")
    assert any(ln.startswith("Style: Title,") for ln in lines) and sum(ln.startswith("Dialogue: 0,") for ln in lines) >= 2


class FakeClient:
    """TEST FIXTURE: the backend's side of the protocol, in memory."""

    def __init__(self, cancel_at: str | None = None) -> None:
        self.stages: list[tuple[str, str, str]] = []
        self.cancel_at = cancel_at
        self.meta: dict[str, Any] = {}
        self.video = b""

    def progress(self, job_id: int, stage: str, status: str, note: str = "") -> bool:
        self.stages.append((stage, status, note))
        return stage != self.cancel_at

    def script(self, job_id: int, script: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "script": {k: script[k] for k in ("segments", "title", "description", "hashtags")}}

    def result(self, job_id: int, video: Path, thumb: Path | None, meta: dict[str, Any]) -> dict[str, Any]:
        self.video, self.meta = video.read_bytes(), meta
        assert thumb is not None and thumb.read_bytes()[:2] == b"\xff\xd8"
        return {"ok": True, "status": "ready"}


def _beeps(texts: list[str], outs: list[Path], voice: str, ffmpeg: str) -> None:
    for k, (t, o) in enumerate(zip(texts, outs)):  # as long as the words would take
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=frequency={300 + 40 * k}:duration={max(1.0, len(t.split()) / 2.6):.2f}",
                        "-ar", "44100", "-ac", "1", "-sample_fmt", "s16", str(o)], check=True)


def _fake_film(skip: int | None):
    def film(prompts: list[str], seconds: list[float], quality: str, workdir: Path, seed: int, on_progress: Any) -> list[Path | None]:
        ff = render.ffmpeg_exe()
        outs: list[Path | None] = []
        for k, s in enumerate(seconds):
            on_progress(k, 0.5)
            if k == skip:
                outs.append(None)  # the model couldn't draw this scene
                continue
            o = workdir / f"shot_{k:02d}.mp4"
            subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=size=480x832:rate={gpu.FPS}", "-frames:v", str(gpu.frames_for(s)),
                            "-pix_fmt", "yuv420p", str(o)], check=True)
            outs.append(o)
        return outs
    return film


def _setup(monkeypatch: pytest.MonkeyPatch, skip: int | None = None, video: bool = True) -> None:
    monkeypatch.setattr(story, "write", lambda job, feedback, attempt: story.template_story(job))  # no LLM in tests
    monkeypatch.setattr(tts, "pick", lambda preference: "kokoro")
    monkeypatch.setattr(tts, "kokoro_batch", _beeps)
    monkeypatch.setattr(gpu, "video_ready", lambda: video)
    monkeypatch.setattr(shorts, "film", _fake_film(skip))


def test_a_short_is_cut_from_clips_voiced_captioned_and_uploaded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _setup(monkeypatch, skip=2)
    c = FakeClient()
    shorts.process(c, JOB, keep=tmp_path)  # type: ignore[arg-type]
    m = c.meta
    assert (m["width"], m["height"]) == (1080, 1920) and m["has_audio"] and m["subtitles"] and m["codec"] == "h264" and m["fps"] == 30
    n = m["scenes"]
    assert m["ai_scenes"] == n - 1 and "wan2.2" in m["renderer"] and "480x832" in m["renderer"] and m["tts"] == "kokoro:am_michael"
    words = len(story.template_story(JOB)["segments"])
    assert n == words and 10 < m["duration"] < 60 and c.video[4:8] == b"ftyp" and (tmp_path / "short7.mp4").exists()
    done = {(s, st) for s, st, _ in c.stages}
    assert {("script", "running"), ("voice", "done"), ("visuals", "done"), ("assemble", "done")} <= done
    assert any(note == f"{n - 1} of {n} scenes animated" for _, _, note in c.stages)
    assert any(note.startswith("scene 1 of") for _, _, note in c.stages)


def test_a_machine_without_the_video_model_still_finishes_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(monkeypatch, video=False)
    c = FakeClient()
    shorts.process(c, JOB)  # type: ignore[arg-type]
    assert c.meta["ai_scenes"] == 0 and c.meta["renderer"] == "stills + ffmpeg" and (c.meta["width"], c.meta["height"]) == (1080, 1920)
    assert ("visuals", "done", "still scenes: this worker has no video model") in c.stages


def test_a_remade_or_deleted_short_stops_the_work(monkeypatch: pytest.MonkeyPatch) -> None:
    _setup(monkeypatch)
    c = FakeClient(cancel_at="visuals")
    with pytest.raises(shorts.Cancelled):
        shorts.process(c, JOB)  # type: ignore[arg-type]
    assert not c.video
