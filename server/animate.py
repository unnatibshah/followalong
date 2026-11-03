"""The instruction video for a manual (CR-006, FR-163): each step is animated from the manual's own figure, so the
learner watches the step happen rather than reading a page.

Per step: a title card (step number, what to do, the parts it uses sliding in) → the figure draws itself on, starting
where the work happens → the camera moves in on that spot with a pulsing ring → it pulls back to the finished figure
and holds (the step's end frame is taken there). A caption band with the instruction stays under the picture.
Frames are drawn with Pillow and numpy and piped to ffmpeg (H.264, 20 fps); nothing leaves the server.
"""

import math
import subprocess
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

from . import guide_video

W, H, CAPTION_H, FPS = 1280, 720, 150, 20
FH = H - CAPTION_H            # the picture area above the caption band
HI = 2                        # the figure is drawn at twice the picture area, so the zoom stays sharp
MAX_ZOOM = 2.6
CARD_S, DRAW_SHARE, OUT_S, HOLD_S = 1.6, 0.42, 0.8, 1.6
INK = (255, 140, 0)           # the highlight ring


def seconds(text: str) -> float:
    """A step's length: long enough to watch it and read its caption (9–16 s)."""
    return round(min(16.0, max(9.0, 7 + 0.3 * len(text.split()))), 2)


def phases(d: float) -> dict:
    """The step's timeline in seconds from its start: card, draw (figure appears), focus (zoomed in), out (zoom back),
    hold (the finished figure; the step's end frame and `done_at` are here)."""
    rest = d - CARD_S - OUT_S - HOLD_S
    draw = rest * DRAW_SHARE
    return {"card": (0.0, CARD_S), "draw": (CARD_S, CARD_S + draw), "focus": (CARD_S + draw, CARD_S + rest),
            "out": (CARD_S + rest, CARD_S + rest + OUT_S), "hold": (d - HOLD_S, d)}


def _font(size: int):
    return ImageFont.load_default(size=size)


def _ease(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int, lines: int) -> list[str]:
    out, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= width or not line:
            line = trial
        else:
            out.append(line)
            line = word
    out.append(line)
    if len(out) > lines:
        out = out[:lines]
        out[-1] = out[-1].rstrip(".,;") + "…"
    return out


def figure(page: Image.Image, box: list[int], size: tuple[int, int], margin: int = 24) -> tuple[Image.Image, tuple]:
    """The step's figure cropped from its page and fitted onto white at `size`, and the page → canvas mapping
    (page px offset x, y, scale, paste x, y) to place other page boxes on it."""
    w, h = page.size
    x0, y0, x1, y1 = box
    pad = 15  # a little margin: boxes tend to clip line ends
    cx0, cy0 = max(0, (x0 - pad) * w // 1000), max(0, (y0 - pad) * h // 1000)
    crop = page.convert("RGB").crop((cx0, cy0, min(w, (x1 + pad) * w // 1000), min(h, (y1 + pad) * h // 1000)))
    fitted = ImageOps.contain(crop, (size[0] - 2 * margin, size[1] - 2 * margin), Image.LANCZOS)
    canvas = Image.new("RGB", size, "white")
    px, py = (size[0] - fitted.width) // 2, (size[1] - fitted.height) // 2
    canvas.paste(fitted, (px, py))
    return canvas, (cx0, cy0, fitted.width / max(1, crop.width), px, py)


def focus_rect(page_size: tuple[int, int], focus: list[int] | None, mapping: tuple, size: tuple[int, int]) -> tuple:
    """Where to zoom on the drawn figure: the focus box (0–1000 of the page) grown by half, at the picture's aspect,
    never closer than MAX_ZOOM, kept inside the picture. The whole picture without a focus box."""
    sw, sh = size
    if not focus:
        return (0.0, 0.0, float(sw), float(sh))
    w, h = page_size
    ox, oy, k, px, py = mapping
    fx0, fy0 = focus[0] * w / 1000, focus[1] * h / 1000
    fx1, fy1 = focus[2] * w / 1000, focus[3] * h / 1000
    cx, cy = px + ((fx0 + fx1) / 2 - ox) * k, py + ((fy0 + fy1) / 2 - oy) * k
    bw, bh = (fx1 - fx0) * k * 1.5, (fy1 - fy0) * k * 1.5
    rw = max(bw, bh * sw / sh, sw / MAX_ZOOM)
    rw = min(rw, sw)
    rh = rw * sh / sw
    x0 = min(max(cx - rw / 2, 0), sw - rw)
    y0 = min(max(cy - rh / 2, 0), sh - rh)
    return (x0, y0, x0 + rw, y0 + rh)


def caption(step: dict, total: int) -> Image.Image:
    band = Image.new("RGB", (W, CAPTION_H), (245, 245, 242))
    d = ImageDraw.Draw(band)
    d.line((0, 0, W, 0), fill=(210, 210, 205), width=2)
    d.text((32, 16), f"Step {step['id']} of {total}  ·  Manual p. {step['manual_page']}", font=_font(22), fill=(90, 90, 90))
    for i, line in enumerate(wrap(d, step["instruction"], _font(32), W - 64, 2)):
        d.text((32, 48 + i * 40), line, font=_font(32), fill=(20, 20, 20))
    return band


def card(step: dict, total: int, t: float) -> Image.Image:
    """The title card at `t` seconds: the step number and instruction, then the parts sliding up one by one."""
    im = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(im)
    a = _ease(t / 0.4)
    grey = lambda v: tuple(int(255 - (255 - v) * a) for _ in range(3))  # noqa: E731  fade in from white
    d.text((80, 150), f"Step {step['id']}", font=_font(96), fill=grey(20))
    d.text((80 + d.textlength(f"Step {step['id']}", font=_font(96)) + 24, 196), f"of {total}", font=_font(40), fill=grey(120))
    for i, line in enumerate(wrap(d, step["instruction"], _font(40), W - 160, 3)):
        d.text((80, 290 + i * 52), line, font=_font(40), fill=grey(40))
    x, f = 80, _font(30)
    for i, p in enumerate(step.get("parts_used") or []):
        k = _ease((t - 0.35 - 0.15 * i) / 0.35)
        if k <= 0:
            break
        label = f"{p['count']}x {p['name']}"  # the default font has no ×
        tw = d.textlength(label, font=f)
        if x + tw + 40 > W - 80:
            break
        y = 520 + (1 - k) * 60
        fill = tuple(int(255 - (255 - c) * k) for c in (255, 244, 214))
        line = tuple(int(255 - (255 - c) * k) for c in (225, 190, 110))
        d.rounded_rectangle((x, y, x + tw + 40, y + 56), radius=12, fill=fill, outline=line, width=2)
        d.text((x + 20, y + 12), label, font=f, fill=tuple(int(255 - (255 - c) * k) for c in (70, 50, 10)))
        x += tw + 56
    return im


class Step:
    """Everything drawn once for a step; `frame(t)` is cheap."""

    def __init__(self, page: Image.Image, step: dict, total: int, clip: list[np.ndarray] | None = None):
        self.step, self.total, self.clip = step, total, clip  # clip: a provider's motion frames (guide_video)
        self._hold = None
        self.d = step["end"] - step["start"]
        self.ph = phases(self.d)
        big, mapping = figure(page, step["figure_box"], (W * HI, FH * HI), margin=24 * HI)
        self.big = big
        self.rect = tuple(v / HI for v in focus_rect(page.size, step.get("focus_box"), mapping, (W * HI, FH * HI)))
        self.band = caption(step, total)
        # The draw-on: each pixel appears at a time set by its distance from the focus (plus a little noise, so lines
        # grow rather than a circle opening).
        small = np.asarray(big.resize((W, FH), Image.BILINEAR), dtype=np.float32)
        self.small = small
        x0, y0, x1, y1 = self.rect
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        self.center = (cx, cy)
        yy, xx = np.mgrid[0:FH, 0:W].astype(np.float32)
        dist = np.hypot(xx - cx, yy - cy)
        rng = np.random.default_rng(step["id"])
        noise = np.asarray(Image.fromarray((rng.random((FH // 16 + 1, W // 16 + 1)) * 255).astype(np.uint8))
                           .resize((W, FH), Image.BILINEAR), dtype=np.float32) / 255
        r = dist / max(1.0, float(dist.max())) * 0.85 + noise * 0.15
        self.reveal = (r / max(1e-6, float(r.max())))[..., None]

    def _view(self, rect: tuple) -> Image.Image:
        x0, y0, x1, y1 = rect
        return self.big.resize((W, FH), Image.BILINEAR, box=(x0 * HI, y0 * HI, x1 * HI, y1 * HI))

    def frame(self, t: float) -> np.ndarray:
        ph = self.ph
        if t < ph["card"][1]:
            im = card(self.step, self.total, t)
            fade = _ease((t - (ph["card"][1] - 0.3)) / 0.3)  # into the white picture area and the caption band
            if fade > 0:
                target = Image.new("RGB", (W, H), "white")
                target.paste(self.band, (0, FH))
                im = Image.blend(im, target, fade)
            return np.asarray(im)
        full = (0.0, 0.0, float(W), float(FH))
        if self.clip and t < ph["out"][1]:  # the provider's motion stretched over draw → focus → out
            p = (t - ph["draw"][0]) / max(1e-6, ph["out"][1] - ph["draw"][0])
            pic = self.clip[min(len(self.clip) - 1, int(p * len(self.clip)))]
            return np.vstack([pic, np.asarray(self.band)])
        if t < ph["draw"][1]:
            p = (t - ph["draw"][0]) / max(1e-6, ph["draw"][1] - ph["draw"][0])
            alpha = np.clip((p * 1.15 - self.reveal) / 0.12, 0, 1)
            pic = (255 - (255 - self.small) * alpha).astype(np.uint8)
            return np.vstack([pic, np.asarray(self.band)])
        if t < ph["focus"][1]:
            k = _ease((t - ph["focus"][0]) / min(1.0, ph["focus"][1] - ph["focus"][0]))
            rect = tuple(a + (b - a) * k for a, b in zip(full, self.rect))
            ring = k > 0.95
        elif t < ph["out"][1]:
            k = _ease((t - ph["out"][0]) / OUT_S)
            rect = tuple(a + (b - a) * k for a, b in zip(self.rect, full))
            ring = False
        else:
            if self._hold is None:  # the finished figure: the same for every frame of the hold
                self._hold = self._compose(self._view(full))
            return self._hold
        return self._compose(self._view(rect), rect if ring and self.rect != full else None, t)

    def _compose(self, im: Image.Image, ring: tuple | None = None, t: float = 0.0) -> np.ndarray:
        if ring:
            x0, y0, x1, y1 = ring
            cx, cy = (self.center[0] - x0) * W / (x1 - x0), (self.center[1] - y0) * FH / (y1 - y0)
            r = min(W, FH) * 0.16 * (1 + 0.08 * math.sin(t * 2 * math.pi * 1.2))
            ImageDraw.Draw(im).ellipse((cx - r, cy - r, cx + r, cy + r), outline=INK, width=6)
        out = Image.new("RGB", (W, H), "white")
        out.paste(im, (0, 0))
        out.paste(self.band, (0, FH))
        return np.asarray(out)

    def end_frame(self) -> Image.Image:
        """The finished figure alone at full size, no text: the step's reference for calibration and the coach."""
        return figure_only(self.big)


def figure_only(big: Image.Image) -> Image.Image:
    canvas = Image.new("RGB", (W, H), "white")
    fitted = ImageOps.contain(big, (W, H), Image.LANCZOS)
    canvas.paste(fitted, ((W - fitted.width) // 2, (H - fitted.height) // 2))
    return canvas


def clip_frames(ffmpeg: str, video: Path, seconds: float, timeout: float) -> list[np.ndarray] | None:
    """A provider's clip as picture-area frames (fitted on white), at most `seconds` of it, or None if it can't be
    read."""
    vf = f"scale={W}:{FH}:force_original_aspect_ratio=decrease,pad={W}:{FH}:(ow-iw)/2:(oh-ih)/2:white,fps={FPS}"
    try:
        raw = subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(video), "-t", f"{seconds:.2f}", "-vf", vf,
                              "-f", "rawvideo",
                              "-pix_fmt", "rgb24", "-"], capture_output=True, timeout=timeout, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    n = len(raw) // (W * FH * 3)
    return [np.frombuffer(raw, np.uint8, W * FH * 3, i * W * FH * 3).reshape(FH, W, 3) for i in range(n)] or None


def encode_args(ffmpeg: str, out: Path) -> list[str]:
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]


def render(pages: list[Path], steps: list[dict], out: Path, ffmpeg: str, timeout: float) -> float:
    """out/video.mp4 and out/frames/step-N.jpg for `steps` (blocking: run it in a thread). Returns the length.
    Drawing has a budget of `timeout` plus twice the video's length (time spent waiting for provider clips is added
    to it, since each provider bounds its own calls); past it the build stops with RuntimeError."""
    (out / "frames").mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(encode_args(ffmpeg, out / "video.mp4"), stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        frame_no, before = 0, Image.new("RGB", (W, FH), "white")
        deadline = time.monotonic() + timeout + 2 * (steps[-1]["end"] if steps else 0)
        for s in steps:
            with Image.open(pages[s["manual_page"] - 1]) as page:
                page.load()
                after = figure(page, s["figure_box"], (W, FH))[0]
                motion = phases(s["end"] - s["start"])["out"][1] - CARD_S
                asked = time.monotonic()
                made = guide_video.clip(before, after, s["instruction"], motion, out / "clips" / str(s["id"]))
                deadline += time.monotonic() - asked
                st = Step(page, s, len(steps), made and clip_frames(ffmpeg, made, motion, timeout))
            st.end_frame().save(out / "frames" / f"step-{s['id']}.jpg", "JPEG", quality=90)
            before = after
            last = round(s["end"] * FPS)  # frame counts from the schedule, so steps never drift
            while frame_no < last:
                if frame_no % FPS == 0 and time.monotonic() > deadline:
                    raise RuntimeError("the video build ran out of time")
                proc.stdin.write(st.frame(frame_no / FPS - s["start"]).tobytes())
                frame_no += 1
        proc.stdin.close()
        err = proc.stderr.read().decode("utf-8", "replace")
        rc = proc.wait(timeout=timeout)
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    if rc != 0:
        raise RuntimeError(err[-300:] or f"ffmpeg exited {rc}")
    return round(frame_no / FPS, 2)
