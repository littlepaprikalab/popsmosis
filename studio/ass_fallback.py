"""The karaoke fallback (docs/PLAN.md, section 7): a watchable song video from ffmpeg alone.

    python -m studio fallback <job-id>

Each lyric line plays over its scene's static art (bundle.direction; plain white when the
art is missing), with the line in a band at the bottom and its words lighting up as they
are sung (ASS \\k timings from timing/song_words.json). Writes out/song_fallback_h.mp4,
1920x1080, H.264 + AAC, -movflags +faststart. Needs audio/song.mp3 and the song's Timing.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from studio import jobs, media
from studio.schemas import Bundle, TimedLine, Timing

OUT = "out/song_fallback_h.mp4"
BAND = 110  # the lyric band at the bottom, as the renderer draws it (section 7)
LEAD_S = 0.5  # a line shows this long before it is sung
HOLD_S = 1.5  # and stays at most this long after
FONT = "Inter"  # falls back to the nearest installed font
FONTS_DIR = Path(__file__).resolve().parent / "render" / "fonts"  # the renderer's, once it lands
SUNG, UNSUNG = "#1F2937", "#9CA3AF"


def make(job: Path) -> Path:
    bundle = jobs.read(job / "bundle.json", Bundle)
    timing = jobs.read(job / media.timing_path("song"), Timing)
    timing = timing.model_copy(update={"lines": [line for line in timing.lines if line.sung]})  # shows what is sung
    song = job / media.audio_path("song")
    if not song.exists():
        raise media.StepFailed(f"no {media.audio_path('song')} to put under the video")
    texts = {line.id: line.text for line in bundle.lyrics}
    scenes = {d.line_id: d.scene for d in bundle.direction}
    shows = show_times(timing)
    out = job / OUT
    out.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="fallback-") as tmp:
        work = Path(tmp)
        (work / "song.ass").write_text(ass(timing, texts, shows), encoding="utf-8")
        (work / "art.txt").write_text(art_list(job, work, timing, scenes, shows), encoding="utf-8")
        width, height = media.VIDEO_WIDTH, media.VIDEO_HEIGHT
        art_height = height - BAND
        subtitles = "ass=song.ass" + (f":fontsdir='{FONTS_DIR}'" if FONTS_DIR.is_dir() else "")
        video_filter = ",".join([
            f"scale={width}:{art_height}:force_original_aspect_ratio=decrease:force_divisible_by=2",
            f"pad={width}:{height}:(ow-iw)/2:(oh-{BAND}-ih)/2:white",
            f"drawbox=x=0:y={art_height}:w={width}:h=2:color=0xE5E7EB:t=fill",
            f"fps={media.FPS}", "format=yuv420p", subtitles,
        ])
        partial = out.with_name(f".{out.stem}.partial.mp4")
        command = ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", "art.txt", "-i", str(song.resolve()),
                   "-vf", video_filter, "-map", "0:v", "-map", "1:a", "-t", f"{timing.duration:.3f}",
                   "-c:v", "libx264", "-preset", "veryfast", "-tune", "stillimage", "-crf", "23",
                   "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(partial.resolve())]
        done = subprocess.run(command, cwd=work, capture_output=True, text=True)
        if done.returncode:
            raise media.StepFailed(f"ffmpeg could not make the fallback video: {done.stderr.strip()[-500:]}")
        os.replace(partial, out)
    return out


def show_times(timing: Timing) -> list[tuple[float, float]]:
    """When each line is on screen: from a little before it is sung to a little after, never two at once."""
    shows = []
    for i, line in enumerate(timing.lines):
        start = max(shows[-1][1] if shows else 0.0, line.start - LEAD_S)
        nxt = timing.lines[i + 1].start if i + 1 < len(timing.lines) else timing.duration + LEAD_S
        end = max(line.end, min(line.end + HOLD_S, nxt - LEAD_S))
        shows.append((start, min(end, timing.duration)))
    return shows


# --- the subtitles ---------------------------------------------------------------------------------


def _ass_color(hex_color: str) -> str:
    r, g, b = hex_color[1:3], hex_color[3:5], hex_color[5:7]
    return f"&H00{b}{g}{r}".upper()


def _clock(t: float) -> str:
    cs = max(0, round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def ass(timing: Timing, texts: dict[str, str], shows: list[tuple[float, float]]) -> str:
    """An ASS file: one event per line, a \\k tag per word. Text before \\k fills in SecondaryColour,
    and each word turns to PrimaryColour as it is sung."""
    font_size = 58
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {media.VIDEO_WIDTH}
PlayResY: {media.VIDEO_HEIGHT}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, \
Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, \
MarginV, Encoding
Style: Lyric,{FONT},{font_size},{_ass_color(SUNG)},{_ass_color(UNSUNG)},&H00FFFFFF,&H00FFFFFF,-1,0,0,0,100,100,0,0,\
1,0,0,2,60,60,{(BAND - font_size) // 2},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = [f"Dialogue: 0,{_clock(start)},{_clock(end)},Lyric,,0,0,0,,{karaoke(line, texts[line.line_id], start)}"
              for line, (start, end) in zip(timing.lines, shows)]
    return head + "\n".join(events) + "\n"


def karaoke(line: TimedLine, text: str, shown_at: float) -> str:
    """The line with a \\k before each word; the line's own punctuation stays on its words."""
    tokens = text.split()
    words = line.words
    if len(words) != len([t for t in tokens if any(c.isalnum() for c in t)]):
        tokens = [w.word for w in words]  # the timing's words do not match the text: show them instead
    out, at, k = [], shown_at, 0
    for token in tokens:
        if k < len(words) and any(c.isalnum() for c in token):
            word = words[k]
            nxt = words[k + 1].start if k + 1 < len(words) else word.end
            if word.start > at:
                out.append(f"{{\\k{_cs(word.start - at)}}}")
            start = max(at, word.start)
            out.append(f"{{\\k{_cs(max(nxt, start) - start)}}}{_escape(token)} ")
            at = max(nxt, start)
            k += 1
        else:
            out.append(f"{_escape(token)} ")
    return "".join(out).rstrip()


def _cs(seconds: float) -> int:
    return max(0, round(seconds * 100))


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


# --- the pictures -----------------------------------------------------------------------------------


def art_list(job: Path, work: Path, timing: Timing, scenes: dict[str, str],
             shows: list[tuple[float, float]]) -> str:
    """An ffconcat list of the art, each scene held from its first line's show time to the next scene's."""
    stretches: list[list] = []  # [scene, start]
    for line, (start, _) in zip(timing.lines, shows):
        scene = scenes.get(line.line_id, "")
        if not stretches or stretches[-1][0] != scene:
            stretches.append([scene, 0.0 if not stretches else start])
    if not stretches:
        stretches = [["", 0.0]]
    rows = ["ffconcat version 1.0"]
    for i, (scene, start) in enumerate(stretches):
        end = stretches[i + 1][1] if i + 1 < len(stretches) else timing.duration
        rows += [f"file '{_picture(job, work, scene)}'", f"duration {max(end - start, 0.04):.3f}"]
    rows.append(f"file '{_picture(job, work, stretches[-1][0])}'")  # the concat demuxer drops the last duration
    return "\n".join(rows) + "\n"


def _picture(job: Path, work: Path, scene: str) -> str:
    art = job / media.art_path(scene)
    if scene and art.exists():
        return str(art.resolve()).replace("'", "'\\''")
    white = work / "white.png"
    if not white.exists():
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        f"color=white:s={media.ART_WIDTH}x{media.ART_HEIGHT}", "-frames:v", "1", str(white)],
                       check=True)
    return str(white)
