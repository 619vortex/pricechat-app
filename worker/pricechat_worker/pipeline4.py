"""Content Studio v4 production: footage first.

  1. research the story's authentic media (assets.py), with rights records
  2. editorial gate: can it carry a good Short? If not, the story goes to editorial review (no filler, no AI pictures)
  3. pick the strongest shots, then write the script for them (script4.py; backend fact + safety checks as before)
  4. narration: AI narrator, the owner's uploaded human narration, or none (on-screen text)
  5. build the edit (editorial.build) or play back the owner's edited timeline (Content Studio's timeline editor)
  6. render (Remotion "Editorial" composition), quality checks + the visual authenticity score

A "timeline" rerender skips 1-5 and renders the owner's timeline as it is (assets are re-fetched from their sources)."""

import shutil
import time
from pathlib import Path
from typing import Any, Callable

from pricechat_worker import assets as media, authenticity, creative, editorial, kiwi, llm, motion, qc2, render, script2, script4, timing, voice2


class EditorialReview(RuntimeError):
    """The story can't be made well with the media available: flagged for an editor, not retried."""

    def __init__(self, msg: str, library: list[dict[str, Any]]) -> None:
        super().__init__(msg)
        self.library = library


def _narrator(job: dict[str, Any], st: dict[str, Any]) -> dict[str, Any]:
    """voice2 settings for the v4 narrators (registered into creative.NARRATORS so voice2 can use them)."""
    creative.NARRATORS.update(editorial.NARRATORS)
    cr = job.get("creative") or {}
    nid = cr.get("narrator") if cr.get("narrator") in editorial.NARRATORS else st["narrator"]
    voice = str(cr.get("narrator") or "")
    if voice not in editorial.NARRATORS and editorial.is_voice(voice):  # a Kokoro voice or mix: the style's own pacing, that voice
        creative.NARRATORS[voice] = {**editorial.NARRATORS[st["narrator"]], "label": voice, "voice": voice}
        nid = voice
    tone = "documentary" if st["id"] in ("luxury", "collectibles") else "breaking"
    return {"narrator": nid, "tone": tone, "pace": "normal", "pronunciations": dict(cr.get("pronunciations") or {})}


def opening_graphic(segments: list[dict[str, Any]], fmt: str, brief: dict[str, Any]) -> None:
    """The visual format decides what the first segment shows: the price card, the real chart or the record card
    (each graphic once, so it moves off any later segment). THEN/NOW and STAKE LINE open on the on-screen line."""
    want = {"PRICE SLAM": next((f"price:{k}" for k in editorial.callouts(brief)), ""), "CHART DRAW": "chart" if editorial.chart_of(brief) else "",
            "RECEIPT": "facts" if editorial.facts_rows(brief)[1] else ""}.get(fmt, "")
    if not want or not segments:
        return
    for s in segments[1:]:
        if str(s.get("overlay") or "").split(":")[0] == want.split(":")[0]:
            s["overlay"] = "none"
    segments[0]["overlay"] = want


def write_script(job: dict[str, Any], shot_list: list[dict[str, Any]], st: dict[str, Any], approve: Callable[[dict[str, Any]], dict[str, Any]],
                 step: Callable[[str, str, str], None]) -> dict[str, Any]:
    feedback: list[str] = []
    for attempt in range(1, 7):
        script = script4.finish(script4.write(job, shot_list, st, feedback, attempt), job)
        ai = script.get("engine") != "template"
        hard = (script2.claim_problems(script, job.get("brief") or {}) or llm.fact_check(job, script)) if ai else []
        soft = script4.style_problems(script) if ai else []
        if hard or (soft and attempt < 4):
            for p in hard + soft:
                if p not in feedback:
                    feedback.append(p)
            del feedback[:-6]
            step("script", "retrying", (hard + soft)[0])
            continue
        verdict = approve(script)
        if verdict.get("ok"):
            return {**script, **verdict["script"], "segments": script["segments"]}
        feedback = (feedback + list(verdict.get("problems") or []))[-6:]
        step("script", "retrying", feedback[-1] if feedback else "")
    raise RuntimeError("Couldn't write a script that passes the fact and safety checks: " + "; ".join(feedback[-2:]))


def _silent_spans(segments: list[dict[str, Any]]) -> list[tuple[float, float]]:
    """No narration: each line is read on screen, at a comfortable reading pace."""
    t, out = 0.3, []
    for s in segments:
        d = max(2.4, len(str(s["text"]).split()) / 2.6 + 0.7)
        out.append((round(t, 3), round(t + d, 3)))
        t += d + 0.25
    return out


def narrate(job: dict[str, Any], segments: list[dict[str, Any]], st: dict[str, Any], ff: str, workdir: Path, mode: str) -> dict[str, Any]:
    """{"spans", "words", "stats", "voice": file or None, "label"}."""
    if mode == "none":
        spans = _silent_spans(segments)
        words, stats = timing.words(segments, spans, None, "clean")
        return {"spans": spans, "words": words, "stats": stats, "voice": None, "label": "no narration"}
    if mode == "upload":
        url = str((job.get("creative") or {}).get("narration_url") or "")
        raw = workdir / "narration_src"
        import httpx

        r = httpx.get(url, timeout=120.0, follow_redirects=True)
        r.raise_for_status()
        raw.write_bytes(r.content)
        voice2._master(ff, raw, workdir / "voice.wav")
        whole = voice2.asr(workdir / "voice.wav")
        if not whole or not whole.get("words"):
            raise RuntimeError("Couldn't time the uploaded narration (speech recognition unavailable).")
        heard = whole["words"]
        counts = [len(str(s["text"]).split()) for s in segments]
        spans, i = [], 0  # each segment = the next N recognised words (the narrator read the approved script)
        for n in counts:
            chunk = heard[min(i, len(heard) - 1):min(len(heard), i + n)] or heard[-1:]
            spans.append((float(chunk[0]["s"]), float(chunk[-1]["e"])))
            i += n
        words, stats = timing.words(segments, spans, whole, "clean")
        return {"spans": spans, "words": words, "stats": stats, "voice": "voice.wav", "label": "uploaded narration"}
    cr = _narrator(job, st)
    rec = voice2.record(segments, cr, ff, workdir)
    asr = voice2.asr(workdir / "voice.wav", rec["key"], rec["spans"])
    spans = [tuple(x) for x in rec["spans"]]
    words, stats = timing.words(segments, spans, asr, "clean")
    return {"spans": spans, "words": words, "stats": stats, "voice": "voice.wav", "label": f"kokoro:{rec['voice']}", "cached": rec.get("cached")}


def produce(job: dict[str, Any], ff: str, workdir: Path, approve: Callable[[dict[str, Any]], dict[str, Any]],
            step: Callable[[str, str, str], None], on_library: Callable[[list[dict[str, Any]]], None] | None = None) -> dict[str, Any]:
    st = editorial.style_of(job)
    cr = job.get("creative") or {}
    mode = cr.get("narration") if cr.get("narration") in editorial.NARRATION_MODES else "ai"
    if "derived" not in (job.get("brief") or {}):  # the backend sends the fact card; offline runs compute the same one
        job = {**job, "brief": {**(job.get("brief") or {}), "derived": kiwi.derived_of(job.get("brief") or {})}}
    timings: dict[str, float] = {}
    pub = workdir / "public"
    pub.mkdir(parents=True, exist_ok=True)
    t = time.monotonic()
    edited = job.get("mode") == "timeline" and (job.get("timeline") or {}).get("video")

    # 1. the media (an edited timeline re-fetches only the assets it uses)
    if edited:
        step("visuals", "running", "fetching the timeline's media")
        want = {c["asset"] for c in job["timeline"]["video"]}
        lib = [dict(a) for a in job.get("media_library") or [] if a.get("id") in want]
        found = []
        with media._client() as c:
            for a in lib:
                got = media.fetch(c, a, pub, ff)
                if not got:
                    raise RuntimeError(f"Couldn't fetch {a.get('title') or a['id']} from {a.get('provider')} again.")
                found.append({**got, "status": "ok"})
        library = [a for a in job.get("media_library") or []]
    else:
        step("visuals", "running", f"researching real media · {st['label']} style")
        found = media.research(job, pub, ff, step)
        library = [media.public_record(a) for a in found]
        if on_library:
            on_library(library)
    timings["media"] = round(time.monotonic() - t, 1)
    amap = {a["id"]: a for a in found if a.get("status") == "ok" and a.get("file")}

    # 2. the editorial gate
    gate = editorial.assess(found, job)
    if not edited and not gate["ok"]:
        raise EditorialReview("Needs editorial review: " + "; ".join(gate["problems"]) + ". Add licensed footage in the media library, or skip this story.",
                              library)
    step("visuals", "done", f"{gate['exact']} exact · {gate['related']} related · {gate['videos']} videos")

    # 3. shots, then the script written for them
    t = time.monotonic()
    shot_list = editorial.shots(found, job, st)
    if job.get("mode") in ("rerender", "timeline") and (job.get("script") or {}).get("segments"):
        script = dict(job["script"])
        known = {s["id"] for s in shot_list}
        if not all(set(s.get("shots") or []) <= known for s in script["segments"]):  # an older script: give each line the next shot
            script["segments"] = [{**s, "shots": [shot_list[k % len(shot_list)]["id"]]} for k, s in enumerate(script["segments"])]
        step("script", "done", "kept the approved script")
    else:
        if cr.get("writer") == "new":  # the Kiwi hook layer: writer -> validators (hard gate) -> critic, then the script opens with it
            step("script", "running", "Kiwi: five hooks")
            p = kiwi.plan(job, list(job.get("history") or []), step)
            if p.get("hold"):
                step("script", "running", f"Kiwi held the hook ({p['reason']}): using the standard writer")
            else:
                job = {**job, "plan": p}
                step("script", "running", f"Kiwi: {p['shape'].lower()} · {p['visual_format']}")
        step("script", "running", f"writing for {len(shot_list)} shots")
        script = write_script(job, shot_list, st, approve, step)
    segments = [dict(s) for s in script["segments"]]
    if script.get("visual_format"):
        job = {**job, "plan": {k: script.get(k) for k in script4.PLAN_FIELDS}}
        opening_graphic(segments, script["visual_format"], job.get("brief") or {})
    timings["script"] = round(time.monotonic() - t, 1)

    # 4. narration
    t = time.monotonic()
    step("voice", "running", editorial.NARRATION_MODES[mode])
    nar = narrate(job, segments, st, ff, workdir, mode)
    timings["voice"] = round(time.monotonic() - t, 1)
    step("voice", "done", nar["label"] + (" · cached" if nar.get("cached") else ""))

    # 5. the edit
    t = time.monotonic()
    if edited:
        tl = dict(job["timeline"])
        tl["captions"] = {**tl.get("captions", {}), "words": nar["words"]}
    else:
        tl = editorial.build(job, st, segments, nar["spans"], nar["words"], shot_list, amap, mode)
        if mode == "none":  # the lines are read on screen instead
            for k, s in enumerate(segments):
                a, b = nar["spans"][k]
                tl["overlays"].append({"id": f"t{k + 1}", "type": "text", "start": a, "dur": b - a + 0.2, "data": {"text": s["text"]}})
    bad = editorial.unsupported(tl, job.get("brief") or {})
    if bad:
        raise RuntimeError("Graphics show numbers that aren't in the research: " + ", ".join(bad[:3]))
    music = tl["music"].get("track")
    motion.public_dir(workdir, music if music in ("documentary", "viral", "playful", "market") else None)
    for f in ("PlayfairDisplay-Variable.ttf", "JetBrainsMono-Variable.ttf"):
        shutil.copy(motion.STATIC / "fonts" / f, pub / "fonts" / f)
    if nar["voice"]:
        shutil.copy(workdir / "voice.wav", pub / "voice.wav")
    props = editorial.props(tl, amap, st, job.get("brand") or {}, "voice.wav" if nar["voice"] else None, f"music/{music}.wav" if music else None)
    step("assemble", "running", f"{len(tl['video'])} shots · {len(tl['overlays'])} graphics")
    out = workdir / "video.mp4"
    stills = [(f"{c['id']}", workdir / f"still_{i:02d}.png", int((c["start"] + c["dur"] / 2) * 30)) for i, c in enumerate(tl["video"])]
    last = [0.0]

    def progress(msg: dict[str, Any]) -> None:
        if msg.get("step") == "render" and msg.get("progress", 0) - last[0] >= 0.05:  # the Studio progress bar shows these
            last[0] = msg["progress"]
            step("assemble", "running", f"render {msg['progress'] * 100:.0f}%")

    done = render_editorial(props, pub, out, [(f, p) for _, p, f in stills], progress)
    timings["render"] = round(time.monotonic() - t, 1)
    meta = render.probe(ff, out)

    # 6. checks
    t = time.monotonic()
    auth = authenticity.score(job, tl, amap, segments, nar["spans"], st)
    checks = quality(ff, out, workdir, meta, props, tl, nar, [(n, p) for n, p, _ in stills], auth)
    timings["qc"] = round(time.monotonic() - t, 1)
    failed = [c for c in checks if c["status"] == "fail"]
    if failed:
        raise RuntimeError("Quality check: " + "; ".join(c["label"] + ": " + c["detail"] for c in failed[:3]))
    thumb = workdir / "thumb.jpg"
    render.poster(ff, workdir, out, thumb, at=min(1.2, props["duration"] / 4))
    step("assemble", "done", f"{meta['duration']:.1f}s · authenticity {auth['score']}/100 · {done.get('seconds', 0):.0f}s render")
    used = {c["asset"] for c in tl["video"]}
    meta.update({"subtitles": bool(nar["words"]), "tts": nar["label"], "llm": script.get("engine"), "renderer": "editorial", "cost_usd": 0.0,
                 "creative": {"production": st["id"], "narration": mode, "narrator": tl["narration"].get("narrator")},
                 "timeline": tl,  # with the caption words, so the editor can draw the caption track
                 "media_library": library, "authenticity": {k: v for k, v in auth.items() if k != "clips"} | {"clips": auth["clips"]},
                 "segments": [{"text": s["text"], "shots": s.get("shots"), "overlay": s.get("overlay"), "beat": s.get("beat")} for s in segments],
                 "shots": shot_list, "gate": gate,
                 "photo_credits": [{"credit": amap[a].get("attribution"), "page": amap[a].get("page"), "license": amap[a].get("license")} for a in used],
                 "checks": checks, "warnings": [c["label"] + ": " + c["detail"] for c in checks if c["status"] == "warn"], "timings": timings,
                 "narration_match": nar["stats"].get("matched")})
    return {"out": out, "thumb": thumb, "meta": meta, "props": props, "timeline": tl, "script": script, "authenticity": auth, "library": library,
            "assets": amap}


def render_editorial(props: dict[str, Any], pub: Path, out: Path, stills: list[tuple[int, Path]], progress: Any) -> dict[str, Any]:
    import json
    import subprocess

    pfile = pub.parent / "props.json"
    pfile.write_text(json.dumps(props), encoding="utf-8")
    args = ["node", str(motion.MOTION / "render.mjs"), str(pfile), str(pub), str(out), "--comp", "Editorial"]
    for frame, path in stills:
        args += ["--still", str(frame), str(path)]
    proc = subprocess.Popen(args, cwd=str(motion.MOTION), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    done: dict[str, Any] = {}
    assert proc.stdout is not None
    for line in proc.stdout:
        if line.startswith("{"):
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("done"):
                done = msg
            elif progress:
                progress(msg)
    proc.wait(timeout=2400)
    err = proc.stderr.read() if proc.stderr else ""
    if proc.returncode != 0 or not done:
        raise RuntimeError("Editorial render failed: " + " | ".join([x for x in err.strip().splitlines() if x.strip()][-3:])[:400])
    return done


def quality(ff: str, out: Path, workdir: Path, meta: dict[str, Any], props: dict[str, Any], tl: dict[str, Any], nar: dict[str, Any],
            stills: list[tuple[str, Path]], auth: dict[str, Any]) -> list[dict[str, Any]]:
    c = qc2._check
    checks = [c("render", "Render completed", meta["width"] == 1080 and meta["height"] == 1920 and meta["has_audio"] and abs(meta["duration"] - props["duration"]) <= 0.4,
                f"{meta['width']}×{meta['height']}, {meta['duration']:.1f}s (planned {props['duration']:.1f}s), audio {'yes' if meta['has_audio'] else 'NO'}", blocking=True)]
    st = nar["stats"]
    if nar["voice"]:
        m = st.get("matched") or 0
        checks.append(c("narration", "Narration matches the script", m >= 0.8 if st.get("asr") else None,
                        f"{m * 100:.0f}% of the script's words recognised" if st.get("asr") else "speech recognition unavailable", blocking=bool(st.get("asr")) and m < 0.6))
    blank, edge = qc2.frame_checks(stills)
    checks.append(c("blank", "No blank shots", not blank, ", ".join(blank) or f"{len(stills)} shots sampled", blocking=bool(blank)))
    m = auth["metrics"]
    checks.append(c("authentic", "Real media carries the video", m["authentic"] >= 0.7, f"{m['authentic'] * 100:.0f}% of screen time on real footage/photos of the story "
                    f"({m['exact'] * 100:.0f}% the exact subject, {m['video_share'] * 100:.0f}% video)", blocking=m["synthetic"] + m["filler"] > 0.5))
    checks.append(c("relevance", "Shots match the narration", m["relevance"] >= 0.65, f"{m['relevance'] * 100:.0f}% ({auth['judge']})"))
    checks.append(c("rights", "Every asset has a rights record", m["verified"] >= 1, f"{m['distinct_assets']} assets, licence + source + credit", blocking=m["verified"] < 1))
    checks.append(c("pacing", "Editing and pacing", auth["parts"]["consistency"] >= 0.7, f"{m['shots']} shots, avg {m['avg_shot']}s ({m['shortest']}–{m['longest']}s)"))
    checks.append(c("resolution", "Pictures are sharp", m["soft"] <= 0.3, f"{m['soft'] * 100:.0f}% of the time enlarged more than 1.6×"))
    checks.append(c("score", "Visual authenticity score", auth["score"] >= 65, f"{auth['score']}/100 ({auth['grade']})" + (f" · {'; '.join(auth['flags'][:2])}" if auth["flags"] else "")))
    lv = qc2.loudness(ff, out)
    checks.append(c("audio", "Audio mixed properly", -19.0 <= lv["lufs"] <= -11.0 and lv["peak"] <= -0.3, f"{lv['lufs']:.1f} LUFS, peak {lv['peak']:.1f} dB"))
    return checks
