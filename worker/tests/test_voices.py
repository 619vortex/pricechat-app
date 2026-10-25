"""Kokoro voice mixes and the editorial narrator accepting any voice (no GPU: the model isn't loaded)."""

import pytest

from pricechat_worker import creative, editorial, kokoro_tts, pipeline4


def test_a_mix_is_weighted_and_normalised() -> None:
    assert kokoro_tts.mix("am_michael") == [("am_michael", 1.0)]
    assert kokoro_tts.mix("am_michael:60+bm_george:40") == [("am_michael", 0.6), ("bm_george", 0.4)]
    assert kokoro_tts.mix("af_heart:1+af_bella:1") == [("af_heart", 0.5), ("af_bella", 0.5)]
    assert kokoro_tts.lang("bm_george:50+am_adam:50") == "en-gb" and kokoro_tts.lang("am_adam") == "en-us"
    for bad in ("", "michael", "am_michael:x", "am_michael:0"):
        with pytest.raises(ValueError):
            kokoro_tts.mix(bad)


def test_editorial_takes_any_voice_with_the_styles_pacing() -> None:
    st = {**editorial.STYLES["real_news"], "id": "real_news"}
    cr = pipeline4._narrator({"creative": {"narrator": "af_nova"}}, st)
    base = editorial.NARRATORS[st["narrator"]]
    assert cr["narrator"] == "af_nova" and creative.NARRATORS["af_nova"]["voice"] == "af_nova"
    assert creative.NARRATORS["af_nova"]["speed"] == base["speed"]
    assert pipeline4._narrator({"creative": {"narrator": "documentary"}}, st)["narrator"] == "documentary"
    assert pipeline4._narrator({"creative": {"narrator": "auto"}}, st)["narrator"] == st["narrator"]
    assert editorial.is_voice("am_michael:60+bm_george:40") and not editorial.is_voice("steady")
