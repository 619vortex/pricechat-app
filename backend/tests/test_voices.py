"""Narrator voices: one catalog for Brand Manager and Short Market, every voice and mix well formed, a preview clip each."""

from pathlib import Path

from app.brand_manager import defaults as bm
from app.content import voices
from app.short_market import defaults as sm

CLIPS = Path(__file__).resolve().parents[2] / "frontend" / "public" / "voices"


def test_every_voice_is_a_known_kokoro_voice_or_a_mix_of_them() -> None:
    assert len(voices.KOKORO) >= 25 and voices.MIXES
    for key in voices.ALL:
        parts = voices.parts(key)
        assert parts and sum(w for _, w in parts) == 100, key
    assert voices.parts("am_michael:60+bm_george:30") is None  # weights must add up
    assert voices.parts("am_nobody") is None and voices.parts("ff_siwis") is None  # unknown or not English
    assert len(max(voices.ALL, key=len)) <= 32  # fits Short Market's voice column and the narrator field


def test_both_products_and_the_editorial_narrator_menu_offer_the_whole_catalog() -> None:
    assert bm.VOICES == voices.ALL and sm.VOICES == voices.ALL and sm.DEFAULT_VOICE in voices.ALL
    assert bm.STRATEGY["voice_name"] in voices.ALL
    assert set(voices.ALL) <= set(bm.CREATIVE_OPTIONS["narrator"]) and "steady" in bm.CREATIVE_OPTIONS["narrator"]


def test_every_voice_has_a_preview_clip() -> None:
    assert voices.preview_slug("am_michael:60+bm_george:40") == "am_michael-60-bm_george-40"
    missing = [k for k in [*voices.ALL, "natural", "steady", "documentary"] if not (CLIPS / f"{voices.preview_slug(k)}.mp3").exists()]
    assert not missing, f"run worker/voice_previews.py: {missing}"
