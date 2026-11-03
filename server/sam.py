"""SAM 3.1 concept segmentation: one short noun phrase ("blue wire", "hand") on one JPEG frame -> instances.

Output grammar and parsing: meta-sam-parser 0.1.0 (Meta's own parser). Masks are placed in frame coordinates at the
box's top-left (TD §4.2); a raster smaller than its box (SAM caps the long side at 1024) is scaled to it.
"""

import asyncio
import base64
import re
import time

import numpy as np
from meta_sam_parser import (CompletedOutcome, SegmentationBoxRecord, SegmentationMaskRecord, decode_mask_to_raster,
                             image_segmentation_format)

from .config import CHECKS, MODELS, TIMEOUTS
from .gateway import gateway

SIZE = re.compile(r";w=(\d+);h=(\d+)")


async def segment(frame_jpeg: bytes, concept: str, session_id: str | None = None) -> dict:
    """{"concept", "instances": [{"box": [x1, y1, x2, y2] (inclusive px), "score", "mask": bool HxW}], "size": (w, h),
    "latency_ms", "cost_usd"}. Instances under `sam_min_confidence` are dropped. Raises GatewayError."""
    t0 = time.monotonic()
    body = {"model": MODELS["segment"],
            "input": [{"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": concept},
                {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(frame_jpeg).decode()}]}],
            "metadata": {"include_confidence": "true", "mask_encoding": "one_bit"}}
    data, usd = await gateway.responses(body, TIMEOUTS["sam_s"], session_id)
    size, instances = await asyncio.to_thread(parse, output_text(data))
    return {"concept": concept, "instances": [i for i in instances if (i["score"] or 0) >= CHECKS["sam_min_confidence"]],
            "size": size, "latency_ms": round((time.monotonic() - t0) * 1000), "cost_usd": usd}


def output_text(data: dict) -> str:
    if isinstance(data.get("output_text"), str):
        return data["output_text"]
    return "".join(c.get("text", "") for item in data.get("output") or [] for c in item.get("content") or []
                   if c.get("type") == "output_text")


def parse(text: str) -> tuple[tuple[int, int] | None, list[dict]]:
    parser = image_segmentation_format().create_parser()
    parser.push(text)
    result = parser.finish(CompletedOutcome()).result
    m = SIZE.search(text)
    if not m:
        return None, []
    w, h = int(m[1]), int(m[2])
    boxes = {r.object_id: r for r in result.records if isinstance(r, SegmentationBoxRecord)}
    out = []
    for r in result.records:
        if isinstance(r, SegmentationMaskRecord) and r.object_id in boxes:
            b = boxes[r.object_id]
            raster = np.frombuffer(decode_mask_to_raster(r.mask), dtype=np.uint8).reshape(r.mask.height, r.mask.width)
            out.append({"box": [int(b.left), int(b.top), int(b.right) - 1, int(b.bottom) - 1],
                        "score": r.confidence, "mask": place(raster, (int(b.left), int(b.top), int(b.right), int(b.bottom)), (w, h))})
    return (w, h), out


def place(raster: np.ndarray, box: tuple[int, int, int, int], size: tuple[int, int]) -> np.ndarray:
    """Paste a box-local raster into a frame-size bool mask. `box` is half-open (left, top, right, bottom)."""
    left, top, right, bottom = box
    h, w = bottom - top, right - left
    if raster.shape != (h, w):  # large boxes come back downsampled (long side capped at 1024): nearest-neighbour up
        raster = raster[(np.arange(h) * raster.shape[0] // h)[:, None], np.arange(w) * raster.shape[1] // w]
    frame = np.zeros((size[1], size[0]), dtype=bool)
    frame[top:bottom, left:right] = raster.astype(bool)
    return frame
