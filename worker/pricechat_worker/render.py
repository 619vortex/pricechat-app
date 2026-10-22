"""Video assembly with FFmpeg: scene clips (slow zoom + fades) → concatenated → animated karaoke captions (ASS) burned
in → narration (+ an optional licensed music bed, ducked) → H.264/AAC 1080×1920 30 fps, fast-start MP4.
FFmpeg comes from PATH, $FFMPEG, or the imageio-ffmpeg package (a static build)."""

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import httpx
from PIL import ImageFont

FPS = 30
PRESET = {"draft": ("veryfast", 25), "standard": ("medium", 21), "high": ("slow", 18)}


def ffmpeg_exe() -> str:
    if os.environ.get("FFMPEG"):
        return os.environ["FFMPEG"]
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as exc:
        raise RuntimeError("FFmpeg not found: install ffmpeg or `pip install imageio-ffmpeg`.") from exc


def run(args: list[str], cwd: Path, timeout: int = 600) -> str:
    p = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {p.stderr.strip().splitlines()[-1] if p.stderr.strip() else p.returncode}")
    return p.stderr


# --- captions ----------------------------------------------------------------------------------------------------------


def _ass_time(t: float) -> str:
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _bgr(rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    return f"&H00{b:02X}{g:02X}{r:02X}"


def captions(segments: list[dict[str, Any]], spans: list[tuple[float, float]], accent: tuple[int, int, int], font_file: str, out: Path,
             titles: bool = False) -> int:
    """Animated captions: 2-4 words at a time, each word lights up in the accent colour as it's spoken (\\k karaoke).
    Word timing is proportional to word length within each spoken segment. `titles` also shows each segment's "screen"
    words big at the top for as long as the segment is spoken (Short Market's scenes are film, not drawn cards)."""
    family = ImageFont.truetype(font_file, 20).getname()[0]
    head = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, "
        "StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Cap,{family},94,{_bgr(accent)},&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,8,3,2,90,180,480,1\n"
        f"Style: Title,{family},104,{_bgr(accent)},&H00FFFFFF,&H00000000,&H78000000,-1,0,0,0,100,100,1,0,1,9,4,8,90,90,290,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    for k, (seg, (start, end)) in enumerate(zip(segments, spans)):
        screen = str(seg.get("screen") or "").strip().upper().replace("{", "(").replace("}", ")")
        if titles and screen:
            until = spans[k + 1][0] if k + 1 < len(spans) else end + 0.3
            lines.append(f"Dialogue: 1,{_ass_time(start)},{_ass_time(until - 0.04)},Title,,0,0,0,,"
                         f"{{\\fad(90,90)\\fscx78\\fscy78\\t(0,160,\\fscx100\\fscy100)}}{screen}")
    for seg, (start, end) in zip(segments, spans):
        words = seg["text"].split()
        if not words:
            continue
        weights = [len(re.sub(r"\W", "", w)) + 2 for w in words]
        total = sum(weights)
        t = start
        times = []
        for w, wt in zip(words, weights):
            d = (end - start) * wt / total
            times.append((w, t, d))
            t += d
        chunk: list[tuple[str, float, float]] = []
        for i, item in enumerate(times):
            chunk.append(item)
            breaks = len(chunk) >= 4 or item[0][-1:] in ".,!?;:" or i == len(times) - 1
            if breaks:
                c0, c1 = chunk[0][1], chunk[-1][1] + chunk[-1][2]
                text = " ".join("{\\k%d}%s" % (max(1, int(round(d * 100))), w.replace("{", "(").replace("}", ")")) for w, _, d in chunk)
                # each chunk pops in (88% → 104% → 100%), so the eye follows the words
                pop = "\\fad(40,40)\\fscx88\\fscy88\\t(0,90,\\fscx104\\fscy104)\\t(90,160,\\fscx100\\fscy100)"
                lines.append(f"Dialogue: 0,{_ass_time(c0)},{_ass_time(c1)},Cap,,0,0,0,,{{{pop}}}{text}")
                chunk = []
    out.write_text(head + "\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


# --- assembly ----------------------------------------------------------------------------------------------------------


def assemble(ffmpeg: str, workdir: Path, scenes: list[Path], spans: list[tuple[float, float]], voice: Path, ass: Path, font_file: str,
             quality: str, music_url: str | None, out: Path, accent: tuple[int, int, int] | None = None) -> None:
    """`accent` also draws a thin progress bar along the top edge (viewers stay when they can see the end coming)."""
    preset, crf = PRESET.get(quality, PRESET["standard"])
    fonts = workdir / "fonts"
    fonts.mkdir(exist_ok=True)
    shutil.copy(font_file, fonts / Path(font_file).name)
    # each scene lasts until the next segment starts (the last one to the end of the narration + a short tail)
    durations = []
    for k, (start, end) in enumerate(spans):
        nxt = spans[k + 1][0] if k + 1 < len(spans) else end + 0.6
        durations.append(max(0.8, nxt - (start if k else 0.0)))
    clips = []
    for k, (img, dur) in enumerate(zip(scenes, durations)):
        frames = int(round(dur * FPS))
        clip = workdir / f"clip_{k:02d}.mp4"
        zoom = "min(zoom+0.0006,1.06)" if k % 2 == 0 else "if(eq(on,0),1.06,max(zoom-0.0006,1.0))"
        vf = (f"scale=1080:1920,zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1080x1920:fps={FPS},"
              f"fade=t=in:st=0:d=0.2,fade=t=out:st={max(0.0, dur - 0.2):.3f}:d=0.2,format=yuv420p")
        run([ffmpeg, "-y", "-loglevel", "error", "-loop", "1", "-i", img.name, "-vf", vf, "-frames:v", str(frames), "-c:v", "libx264",
             "-preset", "veryfast", "-crf", "18", "-r", str(FPS), clip.name], workdir)
        clips.append(clip)
    (workdir / "clips.txt").write_text("".join(f"file '{c.name}'\n" for c in clips), encoding="utf-8")
    run([ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "clips.txt", "-c", "copy", "joined.mp4"], workdir)
    inputs = ["-i", "joined.mp4", "-i", voice.name]
    afilter = "[1:a]apad=pad_dur=0.6[a]"
    if music_url:
        track = workdir / "music.audio"
        r = httpx.get(music_url, timeout=60.0, follow_redirects=True)
        r.raise_for_status()
        track.write_bytes(r.content)
        inputs += ["-stream_loop", "-1", "-i", track.name]
        afilter = ("[1:a]apad=pad_dur=0.6,asplit=2[v][sc];[2:a]volume=0.35[m];[m][sc]sidechaincompress=threshold=0.03:ratio=8[duck];"
                   "[v][duck]amix=inputs=2:duration=first:normalize=0[a]")
    vfilter = f"[0:v]subtitles={ass.name}:fontsdir=fonts[v]"
    if accent:
        total = sum(durations)
        vfilter = (f"color=c=0x{accent[0]:02X}{accent[1]:02X}{accent[2]:02X}:s=1080x10:r={FPS}[bar];"
                   f"[0:v][bar]overlay=x='-w+w*t/{total:.3f}':y=0:shortest=1,subtitles={ass.name}:fontsdir=fonts[v]")
    run([ffmpeg, "-y", "-loglevel", "error", *inputs, "-filter_complex", f"{vfilter};{afilter}",
         "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p", "-r", str(FPS),
         "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-shortest", "-movflags", "+faststart", out.name], workdir, timeout=900)


def thumbnail(ffmpeg: str, workdir: Path, first_scene: Path, out: Path) -> None:
    run([ffmpeg, "-y", "-loglevel", "error", "-i", first_scene.name, "-vf", "scale=540:960", "-q:v", "3", out.name], workdir)


# --- Short Market: scenes are film clips from the video model (or a still when a scene has none) -----------------------


def assemble_story(ffmpeg: str, workdir: Path, media: list[Path], clip_seconds: list[float | None], spans: list[tuple[float, float]],
                   voice: Path, ass: Path, font_file: str, out: Path) -> None:
    """Each scene's clip is fitted to the time its words take (stretched a little, or played forward then backward when
    the words run much longer than the clip), cut together, captioned and voiced. 1080×1920, H.264/AAC, 30 fps."""
    fonts = workdir / "fonts"
    fonts.mkdir(exist_ok=True)
    shutil.copy(font_file, fonts / Path(font_file).name)
    fit = "scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:1920"
    clips = []
    for k, (m, src) in enumerate(zip(media, clip_seconds)):
        start, end = spans[k]
        nxt = spans[k + 1][0] if k + 1 < len(spans) else end + 0.6
        dur = max(0.8, nxt - (start if k else 0.0))
        frames = int(round(dur * FPS))
        clip = workdir / f"cut_{k:02d}.mp4"
        if src:
            loop = ""
            if dur > src * 1.35:  # too long to stretch without it looking slow: forward, then backward
                loop, src = "split[a][b];[b]reverse[r];[a][r]concat=n=2:v=1:a=0,", src * 2
            vf = (f"{loop}setpts={dur / src:.5f}*PTS,fps={FPS},{fit},unsharp=5:5:0.5:5:5:0.0,tpad=stop_mode=clone:stop_duration=1,format=yuv420p")
            run([ffmpeg, "-y", "-loglevel", "error", "-i", m.name, "-vf", vf, "-frames:v", str(frames), "-an", "-c:v", "libx264",
                 "-preset", "veryfast", "-crf", "16", "-r", str(FPS), clip.name], workdir)
        else:
            zoom = "min(zoom+0.0006,1.06)" if k % 2 == 0 else "if(eq(on,0),1.06,max(zoom-0.0006,1.0))"
            vf = f"{fit},zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s=1080x1920:fps={FPS},format=yuv420p"
            run([ffmpeg, "-y", "-loglevel", "error", "-loop", "1", "-i", m.name, "-vf", vf, "-frames:v", str(frames), "-c:v", "libx264",
                 "-preset", "veryfast", "-crf", "16", "-r", str(FPS), clip.name], workdir)
        clips.append(clip)
    (workdir / "cuts.txt").write_text("".join(f"file '{c.name}'\n" for c in clips), encoding="utf-8")
    run([ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", "cuts.txt", "-c", "copy", "story.mp4"], workdir)
    run([ffmpeg, "-y", "-loglevel", "error", "-i", "story.mp4", "-i", voice.name, "-filter_complex",
         f"[0:v]fade=t=in:st=0:d=0.2,subtitles={ass.name}:fontsdir=fonts[v];[1:a]apad=pad_dur=0.6[a]", "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", "-r", str(FPS), "-c:a", "aac", "-b:a", "192k",
         "-ar", "44100", "-shortest", "-movflags", "+faststart", out.name], workdir, timeout=900)


def poster(ffmpeg: str, workdir: Path, video: Path, out: Path, at: float = 0.9) -> None:
    """The thumbnail: a frame of the finished video (title and caption included)."""
    run([ffmpeg, "-y", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", video.name, "-frames:v", "1", "-vf", "scale=540:960", "-q:v", "3", out.name], workdir)


# --- quality check -----------------------------------------------------------------------------------------------------


def probe(ffmpeg: str, path: Path) -> dict[str, Any]:
    p = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)], capture_output=True, text=True, timeout=60)
    info = p.stderr
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", info)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0
    v = re.search(r"Video: (\w+).*?, (\d{2,5})x(\d{2,5})", info)
    fps = re.search(r"(\d+(?:\.\d+)?) fps", info)
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return {"duration": round(dur, 2), "codec": v.group(1) if v else None, "width": int(v.group(2)) if v else 0,
            "height": int(v.group(3)) if v else 0, "fps": float(fps.group(1)) if fps else None, "has_audio": "Audio:" in info,
            "size_bytes": path.stat().st_size, "sha256": h.hexdigest()}
