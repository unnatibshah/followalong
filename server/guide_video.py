"""Who makes the motion in a manual's instruction video (CR-006, FR-163). One call for every provider:

    clip(before, after, prompt, seconds, folder) -> Path to an MP4 that moves from `before` to `after`, or None

`before` is the previous step's finished figure (white for the first step), `after` this step's figure, `prompt` the
step's instruction. `server/animate.py` plays the clip where it would otherwise draw the figure on, then holds the
finished figure as usual; None (or any error) keeps the drawn animation for that step, so a lesson is always made.

FA_GUIDE_VIDEO picks the provider:
- `animated` (default): no clip; the figure is drawn on and zoomed locally. Nothing leaves the server.
- `hf`: a first/last-frame video model on a Hugging Face Space (FA_GUIDE_SPACE, default Wan 2.2), through
  `gradio_client` (`pip install gradio_client`; HF_TOKEN for more than the anonymous GPU quota; FA_GUIDE_TIMEOUT_S per
  clip, default 180). Development only: it sends the manual's figures to that Space, so it is refused on the hosted
  demo.
- `meta`: a Meta video model, when the Model API offers one. `meta_clip` is the one function to fill in; until then
  it returns None and the drawn animation is used.
"""

import logging
import os
import shutil
from pathlib import Path

from PIL import Image

from .config import HOSTED

log = logging.getLogger("followalong.guide_video")

PROVIDER = os.environ.get("FA_GUIDE_VIDEO", "animated").strip().lower()
HF_SPACE = os.environ.get("FA_GUIDE_SPACE", "mcp-tools/wan-2-2-first-last-frame")
HF_ENDPOINT = os.environ.get("FA_GUIDE_ENDPOINT", "/generate_video")
HF_TIMEOUT_S = float(os.environ.get("FA_GUIDE_TIMEOUT_S", "180"))
STYLE = ("Instruction-manual animation, clean black line drawing on white. The parts move smoothly into place to show "
         "this assembly step: ")
NEGATIVE = "colour, photo, shading, text, extra parts, warped lines, flicker, camera shake"


def provider() -> str:
    if PROVIDER not in ("animated", "hf", "meta"):
        log.warning("unknown FA_GUIDE_VIDEO=%r: using the drawn animation", PROVIDER)
        return "animated"
    if PROVIDER == "hf" and HOSTED:  # the learner's manual would go to a third party
        return "animated"
    return PROVIDER


def clip(before: Image.Image, after: Image.Image, prompt: str, seconds: float, folder: Path) -> Path | None:
    """A motion clip from `before` to `after`, or None to keep the drawn animation. Never raises."""
    make = {"hf": hf_clip, "meta": meta_clip}.get(provider())
    if make is None:
        return None
    folder.mkdir(parents=True, exist_ok=True)
    try:
        return make(before, after, prompt, seconds, folder)
    except Exception as e:  # noqa: BLE001 a missing clip is never fatal: the step is drawn instead
        log.warning("guide clip (%s) failed: %s", provider(), str(e)[:300])
        return None


def hf_clip(before: Image.Image, after: Image.Image, prompt: str, seconds: float, folder: Path) -> Path | None:
    from gradio_client import Client, handle_file

    a, b = folder / "before.png", folder / "after.png"
    before.save(a)
    after.save(b)
    client = Client(HF_SPACE, hf_token=os.environ.get("HF_TOKEN") or None, verbose=False)
    job = client.submit(start_image_pil=handle_file(str(a)), end_image_pil=handle_file(str(b)),
                        prompt=STYLE + prompt, negative_prompt=NEGATIVE, duration_seconds=min(5.0, max(2.0, seconds)),
                        api_name=HF_ENDPOINT)
    try:
        result = job.result(timeout=HF_TIMEOUT_S)  # a busy Space queue must not hold the manual's build
    except Exception:
        job.cancel()
        raise
    video = result[0] if isinstance(result, (list, tuple)) else result
    video = video.get("video") or video.get("path") if isinstance(video, dict) else video
    if not video or not Path(video).exists():
        return None
    out = folder / "clip.mp4"
    shutil.copy(video, out)
    return out


def meta_clip(before: Image.Image, after: Image.Image, prompt: str, seconds: float, folder: Path) -> Path | None:
    """The Meta video model, once the Model API has one: send `before`, `after` and STYLE + `prompt`, save the MP4 in
    `folder` and return its path. This runs on the video build's worker thread, so call the API synchronously
    (`httpx.Client` with MODELS["base_url"] and MODEL_API_KEY, timeout HF_TIMEOUT_S), not through the async `gateway`,
    whose client belongs to the server's event loop; log the call's cost the way `gateway` does. Time spent here is
    added to the build's budget, so each call must bound itself."""
    return None
