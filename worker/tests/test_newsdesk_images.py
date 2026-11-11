"""News Desk GPU picture worker: job contract, extend paste-back maths, quality gate. A fake pipeline stands in for the GPU."""

import base64
import io
import random
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw, ImageFilter

from pricechat_worker import newsdesk_images as nd


def photo(w: int, h: int, seed: int = 1) -> Image.Image:
    """A busy, well-exposed test picture (random rectangles): sharp, contrasty, distinctive pixels."""
    rnd = random.Random(seed)
    img = Image.new("RGB", (w, h), (30, 40, 50))
    d = ImageDraw.Draw(img)
    for _ in range(900):
        x, y = rnd.randrange(w), rnd.randrange(h)
        d.rectangle((x, y, x + rnd.randrange(4, 60), y + rnd.randrange(4, 60)), fill=(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)))
    return img


def b64(img: Image.Image, fmt: str = "JPEG") -> str:
    out = io.BytesIO()
    img.save(out, fmt)
    return base64.b64encode(out.getvalue()).decode()


class FakeRenderer:
    """Paints like an edit model that also drifts the centre slightly (as real ones do): the paste-back must undo that exactly."""

    def __init__(self, edits: bool, out: Image.Image | None = None) -> None:
        self.edits, self.repo, self.calls, self.out = edits, "fake/" + ("edit" if edits else "gen"), [], out

    def paint(self, prompt: str, width: int, height: int, seed: int, image: Any = None, negative: Any = None) -> tuple[Image.Image, dict[str, Any]]:
        self.calls.append({"prompt": prompt, "size": (width, height), "seed": seed, "image": image})
        if self.out is not None:
            img = self.out
        elif image is not None:
            drift = image.filter(ImageFilter.GaussianBlur(2)).resize((width - 32, height - 16))  # drift + wrong size
            img = Image.blend(drift, photo(width - 32, height - 16, seed), 0.5)  # and it paints over the margins
        else:
            img = photo(width, height, seed)
        return img, {"seconds": 0.01, "steps": 4, "seed": seed}


class FakeOCR:
    ok = True

    def __init__(self, hits: list[list[str]] | None = None) -> None:
        self.hits, self.boxes = list(hits or []), []

    def lettering(self, img: Any, ignore_box: Any = None) -> list[str]:
        self.boxes.append(ignore_box)
        return self.hits.pop(0) if self.hits else []


# ---------------------------------------------------------------- extend maths

def test_fit_box_full_height_for_a_3_2_photo() -> None:
    box = nd.fit_box((3000, 2000))
    assert box == (120, 0, 1416, 864)  # 1296x864, centred
    assert nd.margin_sides(box) == ["left", "right"]
    assert nd.ai_area_pct(box) == pytest.approx(15.62, abs=0.01)
    assert nd.norm_box(box) == [0.0781, 0.0, 0.9219, 1.0]


def test_fit_box_full_width_for_a_wide_panorama() -> None:
    box = nd.fit_box((3000, 1000))
    assert box[0] == 0 and box[2] == 1536 and box[3] - box[1] == 512
    assert nd.margin_sides(box) == ["top", "bottom"]


def test_ai_area_of_a_16_9_photo_is_zero_and_4_3_is_25_pct() -> None:
    assert nd.ai_area_pct(nd.fit_box((1920, 1080))) == 0.0
    assert nd.ai_area_pct(nd.fit_box((1600, 1200))) == 25.0


def test_paste_back_restores_the_original_pixels_exactly() -> None:
    src = photo(1200, 900)
    scaled, base, box = nd.outpaint_input(src)
    assert base.size == nd.CANVAS and base.getpixel((5, 400)) == nd.FILL  # default fill: grey
    drifted = base.filter(ImageFilter.GaussianBlur(3)).resize((1500, 840))  # the model changes the centre too, and the size
    out, diff = nd.paste_back(drifted, scaled, box)
    assert out.size == nd.CANVAS and diff == 0
    assert out.crop(box).tobytes() == scaled.tobytes()
    assert nd.region_max_diff(drifted.resize(nd.CANVAS), scaled, box) > 0  # without the paste-back it would differ


def test_seam_blend_touches_only_the_painted_margin() -> None:
    src = photo(1200, 900)
    scaled, base, box = nd.outpaint_input(src)
    painted = Image.new("RGB", nd.CANVAS, (0, 0, 0))
    out, diff = nd.paste_back(painted, scaled, box, seam=12)
    assert diff == 0
    x0 = box[0]
    assert out.getpixel((x0 - 1, 300)) != (0, 0, 0)  # next to the photo: blended toward its edge
    assert out.getpixel((x0 - 13, 300)) == (0, 0, 0)  # beyond the seam band: the painting untouched
    assert out.getpixel((5, 300)) == (0, 0, 0)


# ---------------------------------------------------------------- quality gate

def test_quality_passes_a_good_picture_and_names_bad_ones() -> None:
    assert nd.image_quality(photo(1536, 864))[0] == []
    assert any("near black" in p for p in nd.image_quality(Image.new("RGB", (1536, 864), (5, 5, 5)))[0])
    assert any("blown out" in p for p in nd.image_quality(Image.new("RGB", (1536, 864), (250, 250, 250)))[0])
    probs, m = nd.image_quality(Image.new("RGB", (1536, 864), (120, 120, 120)))
    assert any("flat" in p for p in probs) and m["sharpness"] == 0
    blurred = photo(1536, 864).filter(ImageFilter.GaussianBlur(8))
    probs, m = nd.image_quality(blurred)
    assert any("blurry" in p for p in probs), m


def test_laplacian_variance_is_exact_for_a_known_pattern() -> None:
    # vertical stripes 0/200 every pixel at <= 768 wide: Laplacian is +-400 at every pixel -> stored values clip at 128 +- 50, var 2500*64
    img = Image.new("L", (400, 100))
    img.putdata([200 * (x % 2) for _ in range(100) for x in range(400)])
    m = nd.image_quality(img.convert("RGB"))[1]
    assert m["sharpness"] == pytest.approx(160000, rel=0.01)


# ---------------------------------------------------------------- model choice + jobs

def test_model_choice() -> None:
    assert nd.pick_model({"model": "qwen"}, "klein") == "qwen"
    assert nd.pick_model({}, "klein") == "klein"
    assert nd.pick_model({"model": "nope"}, "zimage") == "zimage"
    assert nd.pick_model({"task": "edit"}, "klein") == "qwen_edit"
    assert nd.pick_model({"task": "edit", "model": "nope"}, "klein") == "qwen_edit"
    assert nd.pick_model({"task": "edit", "model": "klein"}, "klein") == "klein"  # authoritative: process() then says it can't edit


def test_old_generate_job_unchanged() -> None:
    r, ocr = FakeRenderer(False), FakeOCR()
    data, res = nd.process({"id": 1, "prompt": "a tanker", "seed": 7, "width": 1536, "height": 864, "model": "klein"}, r, ocr)
    assert Image.open(io.BytesIO(data)).size == (1536, 864)
    assert res["task"] == "generate" and res["seed"] == 7 and res["tries"] == 1 and res["ocr"]["area"] == "all"
    assert {"model", "seconds", "steps", "gpu", "vram_gb", "quality", "total_seconds"} <= set(res)
    assert r.calls[0]["image"] is None and ocr.boxes == [None]


def test_generate_retries_on_lettering_then_fails() -> None:
    r = FakeRenderer(False)
    _, res = nd.process({"prompt": "x", "seed": 3}, r, FakeOCR([["BANK (0.9)"]]))
    assert res["tries"] == 2 and r.calls[1]["seed"] != 3
    with pytest.raises(nd.JobError, match="lettering in every try"):
        nd.process({"prompt": "x", "seed": 3}, FakeRenderer(False), FakeOCR([["A"], ["B"], ["C"]]), tries=3)


def test_bad_quality_is_a_failure_with_reason() -> None:
    with pytest.raises(nd.JobError, match="quality: near black"):
        nd.process({"prompt": "x"}, FakeRenderer(False, out=Image.new("RGB", (1536, 864), (2, 2, 2))), FakeOCR())


def test_extend_job_keeps_the_photo_and_reports_it() -> None:
    src = photo(1500, 1000, seed=5)
    r, ocr = FakeRenderer(True), FakeOCR()
    data, res = nd.process({"task": "edit", "edit": "extend", "image_b64": b64(src, "PNG"), "prompt": "an oil tanker", "seed": 9}, r, ocr)
    assert res["task"] == "edit" and res["edit"] == "extend" and res["preserved_max_diff"] == 0
    assert res["preserved_box"] == [0.0781, 0.0, 0.9219, 1.0] and res["ai_area_pct"] == pytest.approx(15.62, abs=0.01)
    assert res["ocr"]["area"] == "margins" and ocr.boxes == [(120, 0, 1416, 864)]  # OCR sees only the painted margins
    assert r.calls[0]["image"].size == nd.CANVAS and "left and right" in r.calls[0]["prompt"] and "oil tanker" in r.calls[0]["prompt"]
    out = Image.open(io.BytesIO(data))
    assert out.size == nd.CANVAS


def test_extend_refuses_too_much_invented_scenery() -> None:
    r = FakeRenderer(True)
    with pytest.raises(nd.JobError, match="refused.*invent 55%"):  # a portrait photo: 691 of 1536 px wide
        nd.process({"task": "edit", "edit": "extend", "image_b64": b64(photo(800, 1000))}, r)
    assert r.calls == []  # refused before any GPU work
    with pytest.raises(nd.JobError, match="invent 44%"):  # a square photo: 43.75% would be painted
        nd.process({"task": "edit", "edit": "extend", "image_b64": b64(photo(1000, 1000))}, FakeRenderer(True))
    _, res = nd.process({"task": "edit", "edit": "extend", "image_b64": b64(photo(1600, 1200))}, FakeRenderer(True), FakeOCR())  # 4:3 = 25%
    assert res["ai_area_pct"] == 25.0


def test_extend_of_a_16_9_photo_needs_no_model() -> None:
    r = FakeRenderer(True)
    _, res = nd.process({"task": "edit", "edit": "extend", "image_b64": b64(photo(1920, 1080))}, r, FakeOCR())
    assert r.calls == [] and res["ai_area_pct"] == 0.0 and res["preserved_max_diff"] == 0


def test_illustrate_job() -> None:
    r = FakeRenderer(True)
    _, res = nd.process({"task": "edit", "edit": "illustrate", "image_b64": b64(photo(1200, 900)), "prompt": "a central bank"}, r, FakeOCR())
    assert res["ai_illustration"] is True and res["edit"] == "illustrate" and res["ocr"]["area"] == "all"
    assert r.calls[0]["image"].size == nd.CANVAS and "illustration" in r.calls[0]["prompt"]


def test_edit_contract_errors() -> None:
    img = b64(photo(1200, 900))
    with pytest.raises(nd.JobError, match="can't edit"):
        nd.process({"task": "edit", "edit": "extend", "image_b64": img}, FakeRenderer(False))
    with pytest.raises(nd.JobError, match="needs a generator"):
        nd.process({"prompt": "x"}, FakeRenderer(True))
    with pytest.raises(nd.JobError, match="unknown edit"):
        nd.process({"task": "edit", "edit": "enhance", "image_b64": img}, FakeRenderer(True))
    with pytest.raises(nd.JobError, match="needs image_b64 or image_url"):
        nd.process({"task": "edit", "edit": "extend"}, FakeRenderer(True))
    with pytest.raises(nd.JobError, match="https"):
        nd.process({"task": "edit", "edit": "extend", "image_url": "http://example.com/a.jpg"}, FakeRenderer(True))
    with pytest.raises(nd.JobError, match="readable image"):
        nd.process({"task": "edit", "edit": "extend", "image_b64": base64.b64encode(b"not a picture").decode()}, FakeRenderer(True))
    with pytest.raises(nd.JobError, match="unknown task"):
        nd.process({"task": "upscale"}, FakeRenderer(False))


def test_image_url_uses_the_given_fetcher() -> None:
    raw = base64.b64decode(b64(photo(1200, 900)))
    seen = []
    _, res = nd.process({"task": "edit", "edit": "extend", "image_url": "https://upload.wikimedia.org/x.jpg"}, FakeRenderer(True), FakeOCR(),
                        fetch=lambda u: seen.append(u) or raw)
    assert seen == ["https://upload.wikimedia.org/x.jpg"] and res["preserved_max_diff"] == 0


def test_health_lists_models_on_disk(tmp_path: Path) -> None:
    snap = tmp_path / "models--Qwen--Qwen-Image-2512" / "snapshots" / "abc"
    snap.mkdir(parents=True)
    (snap / "model_index.json").write_text("{}")
    part = tmp_path / "models--Qwen--Qwen-Image-Edit-2511"
    (part / "snapshots" / "def").mkdir(parents=True)
    (part / "snapshots" / "def" / "model_index.json").write_text("{}")
    (part / "blobs").mkdir()
    (part / "blobs" / "x.incomplete").write_text("")
    h = nd.Health("klein", True, cache=tmp_path)
    h.done, h.failed, h.last_seconds = 3, 1, 61.2
    info = h.info("qwen")
    assert info["version"] == "1.2" and info["on_disk"] == ["qwen"] and info["loaded"] == "qwen"
    assert info["done"] == 3 and info["failed"] == 1 and info["last_s"] == 61.2 and "qwen_edit" in info["models"]


# ---------------------------------------------------------------- copy on the local LLM

class FakeOllama:
    def __init__(self, replies: list[Any]) -> None:
        self.replies, self.calls = list(replies), []

    def __call__(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        import json

        self.calls.append((url, body))
        if url.endswith("/api/generate"):
            return {}
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return {"message": {"content": r if isinstance(r, str) else json.dumps(r)}}


REQ = {"system": "Write the post as JSON {\"text\": ...}", "user": "Reuters: Brent gave back gains to trade at $88."}


def test_copy_clean_draft_needs_one_check() -> None:
    o = FakeOllama([{"text": "Brent gave back gains"}, {"ok": True, "problems": []}])
    c = nd.write_copy(REQ, post=o, free_gb=lambda: 30.0)
    assert c["draft"] == {"text": "Brent gave back gains"} and c["ok"] is True and c["problems"] == [] and c["rewrites"] == 0
    chat = [b for u, b in o.calls if u.endswith("/api/chat")]
    assert len(chat) == 2 and chat[0]["format"] == "json" and chat[0]["stream"] is False and chat[0]["options"]["temperature"] == 0.4
    assert chat[1]["messages"][0]["content"].startswith("You are a fact checker") and "REPORTING:\nReuters" in chat[1]["messages"][1]["content"]
    assert '"Brent gave back gains"' in chat[1]["messages"][1]["content"]
    assert o.calls[-1] == (f"{nd.OLLAMA}/api/generate", {"model": c["model"], "keep_alive": 0})  # VRAM freed before the picture


def test_copy_rewrites_once_with_the_problems() -> None:
    o = FakeOllama([{"text": "Brent fell"}, {"ok": False, "problems": ["'fell' is not 'gave back gains'"]},
                    {"text": "Brent gave back gains"}, {"ok": True, "problems": []}])
    c = nd.write_copy(REQ, post=o, free_gb=lambda: 30.0)
    chat = [b for u, b in o.calls if u.endswith("/api/chat")]
    assert len(chat) == 4 and c["rewrites"] == 1 and c["ok"] is True and c["draft"] == {"text": "Brent gave back gains"}
    assert chat[2]["messages"][1]["content"].endswith("Fix these problems: 'fell' is not 'gave back gains'")
    assert chat[3]["keep_alive"] == 0


def test_copy_still_failing_after_rewrite_reports_problems() -> None:
    o = FakeOllama([{"t": 1}, {"ok": False, "problems": ["a"]}, {"t": 2}, {"ok": False, "problems": ["b"]}])
    c = nd.write_copy(REQ, post=o, free_gb=lambda: 30.0)
    assert c["ok"] is False and c["problems"] == ["b"] and c["draft"] == {"t": 2}


def test_copy_never_raises() -> None:
    assert nd.write_copy(REQ, post=FakeOllama([ConnectionError("refused")]), free_gb=lambda: 30.0)["error"].startswith("ConnectionError")
    bad = nd.write_copy(REQ, post=FakeOllama(["not json"]), free_gb=lambda: 30.0)
    assert bad["draft"] is None and "JSONDecodeError" in bad["error"]
    assert nd.write_copy({"system": "x"}, post=FakeOllama([]))["error"] == "copy_request has no user message"


def test_copy_frees_the_gpu_or_skips() -> None:
    free = [5.0, 28.0]
    unloaded = []
    o = FakeOllama([{"text": "x"}, {"ok": True}])
    c = nd.write_copy(REQ, post=o, free_gb=lambda: free.pop(0), unload_image_model=lambda: unloaded.append(1))
    assert unloaded == [1] and c["ok"] is True
    c = nd.write_copy(REQ, post=FakeOllama([]), free_gb=lambda: 5.0)
    assert c["draft"] is None and c["error"] == "no vram"


def test_extend_fill_modes_and_unpainted_margins_are_caught() -> None:
    src = photo(1200, 900)
    _, white, box = nd.outpaint_input(src, fill="white")
    _, blur, _ = nd.outpaint_input(src, fill="blur")
    assert white.getpixel((5, 400)) == (255, 255, 255) and blur.getpixel((5, 400)) not in ((255, 255, 255), nd.FILL)
    assert nd.margin_change(white, white, box) == 0.0
    lazy = FakeRenderer(True)
    lazy.paint = lambda prompt, w, h, seed, image=None, negative=None: (image, {"seconds": 0.1, "steps": 4, "seed": seed})  # returns its input
    for fill in ("white", "grey", "blur"):
        with pytest.raises(nd.JobError, match="left the margins unpainted"):
            nd.process({"task": "edit", "edit": "extend", "fill": fill, "image_b64": b64(src)}, lazy, FakeOCR())
    r = FakeRenderer(True)
    _, res = nd.process({"task": "edit", "edit": "extend", "fill": "blur", "image_b64": b64(src)}, r, FakeOCR())
    assert res["fill"] == "blur" and res["margin_change"] >= nd.MIN_MARGIN_CHANGE and "blurred" in r.calls[0]["prompt"]
