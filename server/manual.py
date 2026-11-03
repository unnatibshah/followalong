"""Manual to tutorial (CR-006; documentation/03-design/10-manual-to-tutorial.md): an instruction manual (one PDF or
page photos) → page images → one Muse Spark call (prompt spec P4) → a cleaned plan with each step's page, figure box
and focus box → an instruction video animated from the manual's own figures (`server/animate.py`) whose timeline gives
every step's times exactly. Then the video pipeline's own stages run unchanged (`tutorials._after_plan`: SAM 3.1
references, self-calibration, exact directions), with each step's finished figure as its end frame.

    python -m server.manual build <manual.pdf | page images...> --out DIR [--id X]   # local test run (live)
    python -m server.manual render <manual.pdf> <pages dir>                         # used internally (a subprocess)
    python -m server.manual probe <manual.pdf>                                      # used internally (a subprocess)
"""

import argparse
import asyncio
import base64
import io
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from . import animate, sam
from . import tutorials as tut
from .config import APP, DIR, MODELS, TIMEOUTS, _prompt
from .gateway import CapReached, GatewayError, gateway

PROMPT = _prompt("manual_plan")
SCHEMA = json.loads((DIR / "schemas" / "manual_plan.json").read_text(encoding="utf-8"))
MAX_PAGES, MAX_IMAGES, MAX_STEPS = 40, 20, 40
MAX_PDF_MB, MAX_IMAGE_MB = 20, 10
IMAGE_TYPES = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}
PAGE_PX = 2000            # longest side kept on disk (the video's figures crop from these)
SEND_PX = 1280            # longest side sent to Muse Spark
# Smaller page images until the request fits under the inline limit (50 MB; api-notes: dense 40-page manuals measured
# ~33 MB at 1280 px)
SEND_STEPS, MAX_BODY_MB = ((SEND_PX, 80), (1024, 70), (800, 60)), 40
RENDER_DPI = 150
MIN_BOX = 0.05            # a figure box under 5% of the page is a misread: use the whole page
MIN_FOCUS = 0.002         # a focus box (where the work happens) can be small: a screw hole
RENDER_TIMEOUT_S, PROBE_TIMEOUT_S = 120, 30
# Active content and attachments refused outright (PDFium never runs PDF JavaScript here; this is defence in depth).
# Long names only in the raw scan (a short one like /JS turns up by chance in compressed streams); the probe asks
# PDFium for document JavaScript, attachments and XFA forms, which also finds names hidden in object streams.
UNSAFE = re.compile(rb"/(JavaScript|Launch|EmbeddedFiles?|RichMedia)\b")
Image.MAX_IMAGE_PIXELS = 60_000_000  # decompression bombs: Pillow raises above twice this
MAX_PHOTO_PIXELS = 40_000_000  # a page photo is decoded in the server process: 40 MP is a big phone camera


class Rejected(tut.Rejected):
    """A manual refused before any model call: `invalid_upload`, `invalid_manual` or `too_many_pages`."""


# ---- validation (FR-160) ----

SCRIPTS = "This PDF has scripts or attachments, so we can't open it. Upload photos of the pages instead."


async def check_pdf(path: Path) -> int:
    """Page count of an acceptable PDF. PDFium opens it only in a subprocess with a timeout (`probe`), so a hostile
    file can't crash or hang the server. Raises Rejected."""
    if path.stat().st_size > MAX_PDF_MB * 1024 * 1024:
        raise Rejected("invalid_upload", f"This PDF is over {MAX_PDF_MB} MB. Upload just the pages you need, or photos of them.")
    data = await asyncio.to_thread(path.read_bytes)  # up to 20 MB: off the event loop, with the scan below
    if b"%PDF-" not in data[:1024]:  # readers allow a little junk before the header
        raise Rejected("invalid_upload", "This isn't a PDF. Upload the manual as a PDF or as photos of its pages.")
    if await asyncio.to_thread(UNSAFE.search, data):
        raise Rejected("invalid_manual", SCRIPTS)
    try:  # the answer is the last line of the probe's stderr
        rc, out = await tut._run([sys.executable, "-c", BOOT, "probe", str(path)], PROBE_TIMEOUT_S)
        info = json.loads(out.strip().splitlines()[-1]) if rc == 0 else {}
    except (TimeoutError, ValueError, IndexError):
        info = {}
    if "pages" not in info:
        raise Rejected("invalid_manual", "This PDF is locked. Upload an unlocked copy or photos of the pages."
                       if info.get("locked") else "We couldn't read this PDF. Upload photos of the pages instead.")
    if info["scripts"]:
        raise Rejected("invalid_manual", SCRIPTS)
    n = info["pages"]
    if n == 0:
        raise Rejected("invalid_manual", "This PDF has no pages.")
    if n > MAX_PAGES:
        raise Rejected("too_many_pages", f"This manual has {n} pages. The limit is {MAX_PAGES}: upload just the pages you need.")
    return n


def check_images(paths: list[Path]) -> int:
    """Number of acceptable page images (in the order given). Raises Rejected."""
    if not paths:
        raise Rejected("invalid_upload", "Upload the manual as one PDF or as photos of its pages.")
    if len(paths) > MAX_IMAGES:
        raise Rejected("too_many_pages", f"That's {len(paths)} photos. The limit is {MAX_IMAGES}: upload just the pages you need.")
    for p in paths:
        if p.stat().st_size > MAX_IMAGE_MB * 1024 * 1024:
            raise Rejected("invalid_upload", f"{p.name} is over {MAX_IMAGE_MB} MB.")
        try:
            with Image.open(p) as im:
                kind, pixels = im.format, im.width * im.height
                im.verify()
        except (OSError, SyntaxError, Image.DecompressionBombError) as e:
            raise Rejected("invalid_upload", "One of the photos isn't a picture we can read (JPEG, PNG or WebP).") from e
        if kind not in IMAGE_TYPES.values():
            raise Rejected("invalid_upload", "Photos must be JPEG, PNG or WebP.")
        if pixels > MAX_PHOTO_PIXELS:
            raise Rejected("invalid_upload", f"{p.name} is too large a picture. Use a normal phone photo of the page.")
    return len(paths)


def probe_pdf(pdf: Path) -> dict:
    """{"pages", "scripts"} from PDFium, or {"locked"} (run in a subprocess: `probe`)."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as raw
    try:
        doc = pdfium.PdfDocument(str(pdf))
    except pdfium.PdfiumError as e:
        return {"locked": "password" in str(e).lower()}
    try:
        scripts = (raw.FPDFDoc_GetJavaScriptActionCount(doc.raw) > 0 or raw.FPDFDoc_GetAttachmentCount(doc.raw) > 0
                   or raw.FPDF_GetFormType(doc.raw) >= raw.FORMTYPE_XFA_FULL)
        return {"pages": len(doc), "scripts": bool(scripts)}
    finally:
        doc.close()


# ---- pages ----

def _fit(im: Image.Image, px: int) -> Image.Image:
    im = im.convert("RGB")
    if max(im.size) > px:
        im.thumbnail((px, px), Image.LANCZOS)
    return im


def render_pdf(pdf: Path, out: Path) -> int:
    """PDF → out/NN.png (white background, ≤ PAGE_PX). Run in a subprocess (`render`) so a hostile file can be killed."""
    import pypdfium2 as pdfium
    out.mkdir(parents=True, exist_ok=True)
    doc = pdfium.PdfDocument(str(pdf))
    try:
        for i in range(len(doc)):
            page = doc[i]
            w, h = page.get_size()
            scale = min(RENDER_DPI / 72, PAGE_PX / max(w, h, 1))
            _fit(page.render(scale=scale, fill_color=(255, 255, 255, 255)).to_pil(), PAGE_PX).save(out / f"{i + 1:02d}.png")
            page.close()
        return len(doc)
    finally:
        doc.close()


BOOT = f"import sys; sys.path.insert(0, {str(APP)!r}); from server.manual import main; main()"  # from any working dir


async def pages_from_pdf(pdf: Path, out: Path) -> list[Path]:
    rc, err = await tut._run([sys.executable, "-c", BOOT, "render", str(pdf), str(out)], RENDER_TIMEOUT_S)
    pages = sorted(out.glob("*.png"))
    if rc != 0 or not pages:
        raise tut.Failed("We couldn't read the pages of this PDF. Try photos of the pages instead.") from RuntimeError(err[-300:])
    return pages


def pages_from_images(paths: list[Path], out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    pages = []
    for i, p in enumerate(paths, 1):
        with Image.open(p) as im:
            _fit(ImageOps.exif_transpose(im), PAGE_PX).save(out / f"{i:02d}.png")
        pages.append(out / f"{i:02d}.png")
    return pages


def _jpeg(im: Image.Image, px: int, quality: int = 80) -> bytes:
    buf = io.BytesIO()
    _fit(im, px).save(buf, "JPEG", quality=quality)
    return buf.getvalue()


# ---- the plan (FR-161, FR-162) ----

def plan_request(pages: list[Path], errors: list[str] | None = None) -> dict:
    """The request at the largest page size that keeps the body under MAX_BODY_MB."""
    for px, quality in SEND_STEPS:
        body = _request(pages, errors, px, quality)
        if len(json.dumps(body)) <= MAX_BODY_MB * 1024 * 1024:
            break
    return body


def _request(pages: list[Path], errors: list[str] | None, px: int, quality: int) -> dict:
    content = [{"type": "input_text", "text": PROMPT["user"].replace("{pages}", str(len(pages)))}]
    for i, p in enumerate(pages, 1):
        with Image.open(p) as im:
            jpg = _jpeg(im, px, quality)
        content += [{"type": "input_text", "text": f"Page {i}:"},
                    {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(jpg).decode(),
                     "detail": "high"}]
    if errors:
        content.append({"type": "input_text", "text": "Your previous answer was rejected for these reasons:\n- "
                        + "\n- ".join(errors[:20]) + "\nReturn the whole plan again with these fixed."})
    return {"model": MODELS["plan"], "instructions": PROMPT["system"],
            "input": [{"type": "message", "role": "user", "content": content}],
            "max_output_tokens": MODELS["plan_max_output_tokens"],
            "text": {"format": {"type": "json_schema", "name": "manual_plan", "schema": SCHEMA, "strict": True}}}


def clean_box(box, min_area: float = None) -> list[int]:
    """[x0, y0, x1, y1] in 0–1000 of the page, clamped and ordered; the whole page if missing, malformed or tiny."""
    min_area = MIN_BOX if min_area is None else min_area
    whole = [0, 0, 1000, 1000]
    if not (isinstance(box, list) and len(box) == 4 and all(tut._num(v) for v in box)):
        return whole
    x0, y0, x1, y1 = (min(1000, max(0, int(round(v)))) for v in box)
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    if (x1 - x0) * (y1 - y0) < min_area * 1000 * 1000:
        return whole
    return [x0, y0, x1, y1]


def _parts(v) -> list[dict]:
    out = []
    for p in v if isinstance(v, list) else []:
        if isinstance(p, dict) and tut._str(p.get("name")):
            n = p.get("count")
            out.append({"name": " ".join(p["name"].split())[:60],
                        "count": n if isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= 999 else 1})
    return out[:12]


def clean_manual(raw, pages: int) -> tuple[dict, list[dict]]:
    """The model's manual plan → ({title, parts}, steps in the video plan's shape plus manual_page, figure_box,
    parts_used, warning). Pages clamped to the manual, boxes cleaned, empty and repeated steps dropped, at most
    MAX_STEPS; times from the video's timeline (animate.seconds). Objects and checks go through the video plan's own validation
    (tutorials.clean_plan). Raises tutorials.PlanInvalid."""
    if not isinstance(raw, dict) or not isinstance(raw.get("steps"), list):
        raise tut.PlanInvalid(["the answer must be an object with a steps list"])
    n = pages
    kept, seen = [], set()
    for s in raw["steps"]:
        if not isinstance(s, dict) or not tut._str(s.get("instruction")):
            continue
        page = s.get("page")
        page = min(n, max(1, int(page))) if tut._num(page) else 1
        key = (page, " ".join(s["instruction"].lower().split()))
        if key in seen:
            continue
        seen.add(key)
        fig = clean_box(s.get("figure_box"))
        focus = clean_box(s.get("focus_box"), MIN_FOCUS)
        inside = fig[0] <= focus[0] and fig[1] <= focus[1] and focus[2] <= fig[2] and focus[3] <= fig[3]
        kept.append({**s, "page": page, "figure_box": fig, "focus_box": focus if inside and focus != fig else None})
    kept = kept[:MAX_STEPS]
    if not kept:
        raise tut.PlanInvalid(["no steps: not a manual for a hands-on task"], empty=True)
    t, video_steps = 0.0, []
    for s in kept:  # the animation's own timeline: the loop clip is the close-up, the end frame in the final hold
        d = animate.seconds(s["instruction"])
        focus = animate.phases(d)["focus"]
        video_steps.append({**s, "start": t, "end": t + d, "done_at": t + d - 0.5, "key_clip_start": t + focus[0],
                            "key_clip_end": t + min(focus[1], focus[0] + 3.0)})
        t += d
    steps = tut.clean_plan({"steps": video_steps}, t)
    for s, m in zip(steps, kept):
        s.update(manual_page=m["page"], figure_box=m["figure_box"], focus_box=m["focus_box"],
                 parts_used=_parts(m.get("parts_used")),
                 warning=" ".join(str(m.get("warning") or "").split())[:240])
    title = " ".join(str(raw.get("title") or "").split())[:60]
    return {"title": title, "parts": _parts(raw.get("parts"))}, steps


async def find_steps(pages: list[Path], stats: dict) -> tuple[dict, list[dict]]:
    """One Muse Spark call with every page; one retry with the validation errors (FR-161). Raises Failed."""
    errors = None
    for _ in range(2):
        try:
            body = await asyncio.to_thread(plan_request, pages, errors)  # Pillow work off the event loop
            data, usd = await gateway.responses(body, TIMEOUTS["plan_s"])
        except CapReached:
            raise
        except GatewayError as e:
            raise tut.Failed("Muse Spark didn't answer. Try again in a minute.") from e
        u = data.get("usage") or {}
        stats["cost_usd"] = round(stats.get("cost_usd", 0) + usd, 6)
        stats["calls"] = stats.get("calls", 0) + 1
        stats.update(input_tokens=u.get("input_tokens"), output_tokens=u.get("output_tokens"))
        try:
            return clean_manual(tut._json_text(sam.output_text(data)), len(pages))
        except tut.PlanInvalid as e:
            if e.empty:
                raise tut.Failed("We couldn't find hands-on steps in this manual.") from e
            errors = e.errors
        except (ValueError, TypeError, OverflowError) as e:  # OverflowError: int(Infinity)
            errors = [f"the answer wasn't valid JSON ({e})"]
        stats.setdefault("invalid", []).append(errors)
    raise tut.Failed("We couldn't turn this manual into steps. Try clearer photos or fewer pages.")


# ---- the figures and the video (FR-163) ----

def snap(page: Image.Image, box: list[int]) -> list[int]:
    """Grow the model's box (0–1000) outward until each edge reaches blank paper. Boxes on page photos often stop
    short of the figure (≈10% on tilted shots); a drawing ends where the ink ends, so follow the ink to a blank gap.
    Never shrinks, and stops at a gap, so a neighbouring figure past whitespace stays out."""
    g = np.asarray(page.convert("L"), dtype=np.float32)
    h, w = g.shape
    # Ink = clearly darker than its row's paper; per-row so uneven light on a photo doesn't read as ink.
    ink = g < np.median(g, axis=1, keepdims=True) * 0.6
    # x1, y1 are the last pixel inside the box, so an unchanged box comes back exactly
    x0, y0, x1, y1 = box[0] * w // 1000, box[1] * h // 1000, box[2] * w // 1000 - 1, box[3] * h // 1000 - 1
    gap_y, gap_x = max(3, h // 50), max(3, w // 50)  # a 2% blank band ends the figure

    def grow(edge, step, limit, gap, line):
        last, blank = edge, 0
        i = edge + step
        while 0 <= i < limit and blank < gap and abs(i - edge) <= limit * 12 // 100:
            px = line(i)
            thick = max(8, limit // 100)  # a drawing's line is thin; a table or the page's edge in a photo is wide
            if all(0 <= j < limit and line(j).mean() > 0.3 for j in range(i, i + thick * step, step)):
                break
            if px.sum() > 1:  # ponytail: >1 ink pixel; raise if photo grain ever reads as ink
                last, blank = i, 0
            else:
                blank += 1
            i += step
        return last

    for _ in range(2):  # growing one way widens the band the other way checks
        y1 = grow(y1, 1, h, gap_y, lambda i: ink[i, x0:x1])
        y0 = grow(y0, -1, h, gap_y, lambda i: ink[i, x0:x1])
        x1 = grow(x1, 1, w, gap_x, lambda i: ink[y0:y1, i])
        x0 = grow(x0, -1, w, gap_x, lambda i: ink[y0:y1, i])
    grown = [x0 * 1000 // w, y0 * 1000 // h, -(-(x1 + 1) * 1000 // w), -(-(y1 + 1) * 1000 // h)]
    return [min(grown[0], box[0]), min(grown[1], box[1]), max(grown[2], box[2]), max(grown[3], box[3])]  # never smaller



def snap_boxes(pages: list[Path], steps: list[dict]) -> None:
    """Each step's figure box grown to its drawing's edge (`snap`), so the video and the plan show the whole figure.
    Snapping only grows a box, so the focus box stays inside it."""
    for s in steps:
        with Image.open(pages[s["manual_page"] - 1]) as page:
            s["figure_box"] = snap(page, s["figure_box"])


async def build_video(pages: list[Path], steps: list[dict], out: Path) -> float:
    """video.mp4 (each step animated from its figure: server/animate.py) and frames/step-N.jpg (the end frames).
    Returns the video's length."""
    try:
        await asyncio.to_thread(snap_boxes, pages, steps)
        return await asyncio.to_thread(animate.render, pages, steps, out, tut.ffmpeg(), tut.TIMEOUTS["ffmpeg_s"])
    except (OSError, RuntimeError, subprocess.SubprocessError) as e:
        raise tut.Failed("We couldn't build the video for this manual.") from e


def notes(steps: list[dict]) -> str:
    """The manual's own words for the directions pass, in the narration's "[seconds] words" form (FR-168)."""
    return "\n".join(f"[{s['start']:.0f} s] Manual page {s['manual_page']}: {s['instruction']}"
                     + (f" Warning: {s['warning']}" if s["warning"] else "") for s in steps)


# ---- the pipeline (FR-164) ----

async def process(src: Path, kind: str, out: Path, tid: str, progress=lambda text: None) -> tuple[dict, dict]:
    """A validated manual (`src`: the PDF, or a folder of page images in order) → out/ (pages/, frames/, clips/,
    refs/, video.mp4) and the plan (not saved). Returns (plan, stats). Raises Failed or CapReached."""
    stats, stages = {}, {}
    out.mkdir(parents=True, exist_ok=True)
    progress("finding_steps 0/0")
    t = time.monotonic()
    pages = (await pages_from_pdf(src, out / "pages") if kind == "pdf"
             else await asyncio.to_thread(pages_from_images, sorted(p for p in src.iterdir() if p.is_file()), out / "pages"))
    stages["pages"] = round((time.monotonic() - t) * 1000)
    t = time.monotonic()
    info, steps = await find_steps(pages, stats)
    stages["muse_spark"] = round((time.monotonic() - t) * 1000)
    t = time.monotonic()
    duration = await build_video(pages, steps, out)
    stages["video"] = round((time.monotonic() - t) * 1000)
    plan, stats = await tut._after_plan(tid, steps, out / "video.mp4", out, duration, notes(steps), stats, stages,
                                        progress, {}, frames_ready=True)
    parts = [f"{p['name']} ×{p['count']}" if p["count"] > 1 else p["name"] for p in info["parts"]]
    plan.update(source="manual", transcript_used=False, manual={"kind": kind, "pages": len(pages),
                                                                "title": info["title"]}, needs=parts or plan["needs"])
    stats.update(duration_s=duration, pages=len(pages))
    return tut.rehash(plan), stats


# ---- CLI ----

async def _build(a) -> None:
    out = Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    files = [Path(f) for f in a.files]
    if len(files) == 1 and files[0].suffix.lower() == ".pdf":
        await check_pdf(files[0])
        src, kind = files[0], "pdf"
    else:
        check_images(files)
        src, kind = out.parent / f"{out.name}-src", "images"
        shutil.rmtree(src, ignore_errors=True)
        src.mkdir(parents=True)
        for i, f in enumerate(files, 1):
            shutil.copy(f, src / f"{i:02d}{f.suffix.lower()}")
    t = time.monotonic()
    plan, stats = await process(src, kind, out, a.id, lambda p: print("  ", p, flush=True))
    (out / "plan.json").write_text(json.dumps(plan, indent=1, ensure_ascii=False), encoding="utf-8")
    await asyncio.gather(*tut._background)  # the directions pass
    print(json.dumps({"steps": len(plan["steps"]), "seconds": round(time.monotonic() - t, 1), **stats}, indent=1))
    print(tut.summary(plan))


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("files", nargs="+")
    b.add_argument("--out", required=True)
    b.add_argument("--id", default="manual-test")
    r = sub.add_parser("render")
    r.add_argument("pdf")
    r.add_argument("out")
    sub.add_parser("probe").add_argument("pdf")
    a = ap.parse_args(argv)
    if a.cmd == "probe":
        print(json.dumps(probe_pdf(Path(a.pdf))), file=sys.stderr)  # tut._run keeps stderr
    elif a.cmd == "render":
        render_pdf(Path(a.pdf), Path(a.out))
    else:
        asyncio.run(_build(a))


if __name__ == "__main__":
    main()
