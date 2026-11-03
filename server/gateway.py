"""The only code that calls Meta (engineering standards §2). Allow-list (FR-153), timeouts and one retry (NFR-30),
cost from `usage` (FR-120), spend counters, one JSON log line per call. Never logs the key, frames or text."""

import asyncio
import contextlib
import json
import logging
import os
import random
import time
from pathlib import Path
import httpx2 as httpx
import websockets

from .config import CAPS, HOSTED, MODELS, PRICES, RUNTIME, TIMEOUTS
from .store import et_now, locked_json, secs_to_et_midnight

log = logging.getLogger("followalong.gateway")
NO_RETRY = {400, 401, 402, 403, 404, 413}


class GatewayError(Exception):
    def __init__(self, code: str, status: int | None = None):
        super().__init__(code if status is None else f"{code} ({status})")
        self.code, self.status = code, status


class CapReached(GatewayError):
    """A dollar cap would be crossed (FR-121). Not a model failure: the API answers 429 cap_reached."""

    def __init__(self, which: str, retry_after_s: int | None = None):
        super().__init__("cap_reached")
        self.which, self.retry_after_s = which, retry_after_s


class Busy(GatewayError):
    """The API's request budget is spent right now; a background call (a live-coach look) is shed, not queued."""

    def __init__(self):
        super().__init__("busy")


class RateLimiter:
    """All Meta calls share one key, limited to `rpm` requests a minute (60 for our key). A sliding window of start
    times. Interactive calls (checks, questions, voice, tutorial processing) wait up to `max_wait_s` for a slot;
    background calls (the live coach's looks) only run while the window is under `background_share` of `rpm`, so
    they can never starve a learner's check, and are shed (Busy) instead of queued: a stale look is useless.
    ponytail: in-process only. Several server processes on one key need a shared store (Redis) for this window."""

    def __init__(self, rpm: int, background_share: float = 0.7, max_wait_s: float = 20, clock=time.monotonic,
                 sleep=asyncio.sleep):
        self.rpm, self.share, self.max_wait_s, self.clock, self.sleep = rpm, background_share, max_wait_s, clock, sleep
        self.starts: list[float] = []
        self.shed = 0

    def _trim(self) -> None:
        cutoff = self.clock() - 60
        while self.starts and self.starts[0] <= cutoff:
            self.starts.pop(0)

    def load(self) -> float:
        """Share of the minute's budget already used (0..1)."""
        self._trim()
        return len(self.starts) / self.rpm

    async def acquire(self, priority: str = "interactive") -> None:
        if priority == "background":
            if self.load() >= self.share:
                self.shed += 1
                raise Busy()
            self.starts.append(self.clock())
            return
        waited = 0.0
        while self.load() >= 1:
            pause = max(0.05, self.starts[0] + 60 - self.clock())
            if waited + pause > self.max_wait_s:
                raise Busy()
            await self.sleep(pause)
            waited += pause
        self.starts.append(self.clock())

    def next_look_ms(self) -> int:
        """How soon a learner's live coach should look again: every 1.5 s when the key has room, slower as it fills."""
        load = self.load()
        return 1500 if load < 0.4 else 3000 if load < 0.55 else 6000


class Metrics:
    """Recent model calls in memory for /api/metrics: latency percentiles, errors and cost per model. Never prompts,
    frames or text."""

    def __init__(self, keep: int = 2000):
        from collections import deque
        self.calls = deque(maxlen=keep)

    def add(self, model: str, latency_ms: int, usd: float, error: str | None) -> None:
        self.calls.append((time.time(), model, latency_ms, usd, error))

    def summary(self, window_s: float = 3600) -> dict:
        since = time.time() - window_s
        out: dict[str, dict] = {}
        for at, model, ms, usd, err in self.calls:
            if at < since:
                continue
            m = out.setdefault(model, {"calls": 0, "errors": 0, "cost_usd": 0.0, "_ms": []})
            m["calls"] += 1
            m["errors"] += bool(err)
            m["cost_usd"] = round(m["cost_usd"] + usd, 6)
            if not err:
                m["_ms"].append(ms)
        for m in out.values():
            ms = sorted(m.pop("_ms"))
            pick = lambda q: ms[min(len(ms) - 1, int(q * len(ms)))] if ms else None  # noqa: E731
            m.update(p50_ms=pick(0.5), p95_ms=pick(0.95))
        return out


def has_key() -> bool:
    return bool(os.environ.get("MODEL_API_KEY"))


def cost(model: str, usage: dict) -> float:
    if model.startswith("muse-voice"):
        return PRICES["voice_per_hour"] * (usage.get("audio_ms") or 0) / 3_600_000
    if model.startswith("sam-"):
        return PRICES["sam_per_image"] * (usage.get("images_processed") or 1)
    if model.startswith("muse-spark"):
        cached = (usage.get("input_tokens_details") or {}).get("cached_tokens") or 0  # nulls are 0, not a crash
        return (((usage.get("input_tokens") or 0) - cached) * PRICES["spark_input_per_m"]
                + cached * PRICES["spark_cached_per_m"]
                + (usage.get("output_tokens") or 0) * PRICES["spark_output_per_m"]) / 1e6
    return 0.0


def estimate(model: str, body: dict | None = None) -> float:
    """Upper-ish cost of the next call, for the cap check: one SAM image, or Muse Spark's whole output budget."""
    if model.startswith("sam-"):
        return PRICES["sam_per_image"]
    if model.startswith("muse-voice"):
        return cost(model, {"audio_ms": (body or {}).get("audio_s", 0) * 1000})
    return (body or {}).get("max_output_tokens", MODELS["judged_max_output_tokens"]) * PRICES["spark_output_per_m"] / 1e6


class Budget:
    """data/budget.json (data dictionary): spend per session, per ET day and in total, saved after every call under a
    file lock so it survives restarts and redeploys (FR-121). Always counted; caps enforced only when `enforce`
    (FA_HOSTED=true). Delete the file to reset the counters (configuration guide §4).
    A call `reserve`s its estimate (cap check and add, in one locked write) and `settle`s the real cost afterwards, so
    concurrent sessions can't overshoot a cap; a crash mid-call leaves the estimate counted (the safe side)."""

    def __init__(self, path: Path, caps: dict, enforce: bool, clock=et_now):
        self.path, self.caps, self.enforce, self.clock = path, caps, enforce, clock

    def _fresh(self, b: dict) -> dict:
        day = self.clock().date().isoformat()
        if b.get("day") != day:
            b.update(day=day, day_usd=0.0)
        b.setdefault("total_usd", 0.0)
        b.setdefault("sessions", {})
        b.setdefault("audio_s", {})  # voice seconds per session (FA_VOICE_MAX_SECONDS, security T15)
        b["caps"] = self.caps
        return b

    def snapshot(self) -> dict:
        with locked_json(self.path, {}) as b:
            return dict(self._fresh(b))

    def _refuse(self, b: dict, session_id: str | None, est: float) -> None:
        """Raise CapReached if `est` more would cross a cap. Sessionless calls (tutorial processing) skip the session cap."""
        if not self.enforce:
            return
        if session_id and b["sessions"].get(session_id, 0.0) + est > self.caps["session"]:
            raise CapReached("session")
        if b["day_usd"] + est > self.caps["day"]:
            raise CapReached("day", secs_to_et_midnight(self.clock()))
        if b["total_usd"] + est > self.caps["total"]:
            raise CapReached("total")

    def check(self, session_id: str | None, est: float) -> None:
        """Cap check only, nothing reserved (the upload route's early refusal; its model calls reserve their own)."""
        self._refuse(self.snapshot(), session_id, est)

    def reserve(self, session_id: str | None, est: float) -> None:
        """Check and count `est` in one locked write; raise CapReached (nothing counted) if it would cross a cap."""
        with locked_json(self.path, {}) as b:
            self._refuse(self._fresh(b), session_id, est)  # raising inside the block skips the write-back
            self._add(b, session_id, est)

    def settle(self, session_id: str | None, est: float, usd: float) -> None:
        """Replace a reservation with the real cost (0 for a failed call)."""
        self.charge(session_id, usd - est)

    def charge(self, session_id: str | None, usd: float) -> None:
        with locked_json(self.path, {}) as b:
            self._add(self._fresh(b), session_id, usd)

    @staticmethod
    def _add(b: dict, session_id: str | None, usd: float) -> None:
        b["day_usd"] = max(0.0, round(b["day_usd"] + usd, 6))  # a reservation settled after ET midnight
        b["total_usd"] = round(b["total_usd"] + usd, 6)
        if session_id:
            b["sessions"][session_id] = round(b["sessions"].get(session_id, 0.0) + usd, 6)

    def audio_used(self, session_id: str) -> float:
        return self.snapshot()["audio_s"].get(session_id, 0.0)

    def add_audio(self, session_id: str, seconds: float) -> None:
        if seconds:
            with locked_json(self.path, {}) as b:
                a = self._fresh(b)["audio_s"]
                a[session_id] = round(a.get(session_id, 0.0) + seconds, 3)

    def remaining_day(self) -> float:
        return round(max(0.0, self.caps["day"] - self.snapshot()["day_usd"]), 4)


class Gateway:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, sleep=asyncio.sleep, budget: Budget | None = None,
                 ws_connect=websockets.connect):
        self._transport, self._sleep, self._client, self._ws_connect = transport, sleep, None, ws_connect
        self._loop = None
        self.budget = budget or Budget(RUNTIME / "budget.json", CAPS, HOSTED)
        self.limiter = RateLimiter(int(os.environ.get("FA_API_RPM", 60)))
        self.metrics = Metrics()

    def _http(self) -> httpx.AsyncClient:
        """One pooled client per event loop: reusing the TLS connection saves ~100 ms per call. A client is bound to
        the loop it was made on, so a CLI or test that calls asyncio.run() twice gets a new one (DEF-001)."""
        loop = asyncio.get_running_loop()
        if self._client is None or self._loop is not loop:
            self._client, self._loop = httpx.AsyncClient(base_url=MODELS["base_url"], transport=self._transport), loop
        return self._client

    async def responses(self, body: dict, timeout_s: float, session_id: str | None = None,
                        priority: str = "interactive") -> tuple[dict, float]:
        """POST /v1/responses. Returns (response JSON, cost in USD). Raises GatewayError (Busy when shed)."""
        model = body.get("model")
        return await self._post(model, "/responses", timeout_s, session_id, estimate(model, body),
                                priority=priority, json={**body, "store": False})

    async def transcribe(self, wav: bytes, seconds: float, session_id: str | None = None, mode: str = "PUSH_TO_TALK",
                         timeout_s: float | None = None, languages: list[str] | None = None) -> tuple[dict, float]:
        """POST /v1/asr/transcribe (multipart `request` JSON + `audio` WAV): a push-to-talk clip, or (mode
        ENDPOINTING, with `turns`) a tutorial's narration. Cost per audio second, from `audioDurationMs`.
        Returns (response JSON, cost in USD)."""
        model = MODELS["voice"]
        req = json.dumps({"model": model, "audioEncoding": "WAV", "mode": mode, "languageBias": languages or ["English"]})
        return await self._post(model, "/asr/transcribe", timeout_s or TIMEOUTS["transcribe_s"], session_id,
                                estimate(model, {"audio_s": seconds}), headers={"Accept": "application/json"},
                                files={"request": (None, req, "application/json"), "audio": ("clip.wav", wav, "audio/wav")})

    async def _post(self, model: str, path: str, timeout_s: float, session_id: str | None, est: float,
                    headers: dict | None = None, priority: str = "interactive", **send) -> tuple[dict, float]:
        if model not in MODELS["allowed"] or "-contributor" in str(model):
            raise GatewayError("model_not_allowed")
        key = self._key()
        await self.limiter.acquire(priority)  # the key's 60 requests/minute, shared by every learner
        self.budget.reserve(session_id, est)  # before any network call (FR-121); settled with the real cost
        usd = 0.0
        try:
            for attempt in range(TIMEOUTS["retries"] + 1):
                t0 = time.monotonic()
                status, wait = None, None
                if attempt:
                    await self.limiter.acquire("interactive")  # a retry is another request against the limit
                try:
                    r = await self._http().post(path, timeout=timeout_s, headers={"Authorization": f"Bearer {key}", **(headers or {})},
                                                **send)
                    status = r.status_code
                    if status == 200:
                        data = r.json()
                        if not isinstance(data, dict):
                            raise ValueError("not a JSON object")
                        usd = cost(model, data.get("usage") or {"audio_ms": data.get("audioDurationMs", 0)})
                        u = data.get("usage") or {}
                        self._log(model, t0, usd, reasoning=(u.get("output_tokens_details") or {}).get("reasoning_tokens"),
                                  frames=u.get("video_frames_processed"))
                        return data, usd
                    if status in NO_RETRY:
                        self._log(model, t0, 0.0, f"http_{status}")
                        raise GatewayError("http_error", status)
                    after = r.headers.get("retry-after")
                    wait = float(after) if after and after.replace(".", "", 1).isdigit() else None
                    self._log(model, t0, 0.0, f"http_{status}")
                except httpx.TimeoutException:
                    self._log(model, t0, 0.0, "timeout")
                except httpx.TransportError:
                    self._log(model, t0, 0.0, "network")
                except (ValueError, TypeError, AttributeError):  # a malformed 200 (an HTML page, an array): a failed try
                    self._log(model, t0, 0.0, "invalid_response")
                if attempt == TIMEOUTS["retries"] or (wait is not None and wait > timeout_s):
                    break  # a Retry-After longer than the call's timeout goes straight to the fallback (FR-150)
                await self._sleep(wait if wait is not None else TIMEOUTS["backoff_ms"] / 1000 * (1 + random.random()))
            raise GatewayError("failed", status)
        finally:  # a failure (or a cancelled call) refunds the reservation
            self.budget.settle(session_id, est, usd)

    @contextlib.asynccontextmanager
    async def realtime(self, session_id: str, keywords: list[str], languages: list[str] | None = None):
        """Muse Voice Transcribe realtime (`wss://…/asr/realtime`, ENDPOINTING, PCM_24KHZ). The key goes in the first
        JSON text frame, never in a header or to the browser. Yields (upstream socket, meter); add the PCM bytes you
        send to meter["bytes"]: they are charged per audio second when the block exits. Raises
        GatewayError("voice_unavailable") if the connection or the handshake is refused."""
        model = MODELS["voice"]
        if model not in MODELS["allowed"]:
            raise GatewayError("model_not_allowed")
        key = self._key()
        await self.limiter.acquire()
        est = estimate(model, {"audio_s": 60})  # reserved, then settled with the real audio seconds at the end
        self.budget.reserve(session_id, est)
        meter = {"bytes": 0}
        try:
            url = MODELS["base_url"].replace("https://", "wss://", 1) + "/asr/realtime"
            t0 = time.monotonic()
            try:
                ws = await self._ws_connect(url, open_timeout=TIMEOUTS["voice_connect_s"])
            except (OSError, TimeoutError, websockets.WebSocketException) as e:
                self._log(model, t0, 0.0, f"connect_{type(e).__name__}")
                raise GatewayError("voice_unavailable") from e
            try:
                await ws.send(json.dumps({"authorization": {"accessToken": f"Bearer {key}"}, "audioEncoding": "PCM_24KHZ",
                                          "model": model, "mode": "ENDPOINTING", "keywords": keywords,
                                          "languageBias": languages or ["English"], "emitAudioProgress": False}))
                ack = json.loads(await asyncio.wait_for(ws.recv(), TIMEOUTS["voice_connect_s"]))
                if ack.get("type") or not ack.get("sessionId"):  # the acknowledgement is the only frame without a `type`; an error has one
                    raise ValueError(ack.get("errorCode") or ack.get("type"))
            except (OSError, TimeoutError, ValueError, websockets.WebSocketException) as e:
                await ws.close()
                self._log(model, t0, 0.0, "handshake_refused")
                raise GatewayError("voice_unavailable") from e
            try:
                yield ws, meter
            finally:
                await ws.close()
                self._log(model, t0, cost(model, {"audio_ms": meter["bytes"] / 48}), audio_s=round(meter["bytes"] / 48000, 1))
        finally:  # 48 bytes per ms at 24 kHz, 16-bit; a refused connection refunds the reservation
            self.budget.settle(session_id, est, cost(model, {"audio_ms": meter["bytes"] / 48}))

    def _key(self) -> str:
        key = os.environ.get("MODEL_API_KEY")
        if not key:
            raise GatewayError("no_api_key")
        return key

    async def upload_file(self, path: Path, ttl_s: int = 86400) -> str:
        """POST /v1/files (purpose user_data, expiring after `ttl_s`; FR-03, FR-08). Free; no retry. Returns the id."""
        t0 = time.monotonic()
        with open(path, "rb") as f:
            try:
                r = await self._http().post(
                    "/files", timeout=TIMEOUTS["files_s"], headers={"Authorization": f"Bearer {self._key()}"},
                    data={"purpose": "user_data", "expires_after[anchor]": "created_at",
                          "expires_after[seconds]": str(ttl_s)},
                    files={"file": (path.name, f, "video/mp4")})
            except httpx.HTTPError as e:
                self._log("files", t0, 0.0, type(e).__name__)
                raise GatewayError("failed") from e
        self._log("files", t0, 0.0, None if r.status_code == 200 else f"http_{r.status_code}")
        if r.status_code != 200:
            raise GatewayError("http_error", r.status_code)
        return r.json()["id"]

    async def delete_file(self, file_id: str) -> None:
        """DELETE /v1/files/{id}; a failure is logged only (the file expires on its own)."""
        t0 = time.monotonic()
        try:
            r = await self._http().delete(f"/files/{file_id}", timeout=30, headers={"Authorization": f"Bearer {self._key()}"})
            err = None if r.status_code in (200, 204, 404) else f"http_{r.status_code}"
        except (httpx.HTTPError, GatewayError) as e:
            err = type(e).__name__
        self._log("files_delete", t0, 0.0, err)

    async def input_tokens(self, body: dict) -> int:
        """POST /v1/responses/input_tokens: the exact input count for a request, without running it (free)."""
        if body.get("model") not in MODELS["allowed"]:
            raise GatewayError("model_not_allowed")
        keep = {k: body[k] for k in ("model", "input", "instructions", "text", "reasoning") if k in body}
        r = await self._http().post("/responses/input_tokens", json=keep, timeout=60,
                                    headers={"Authorization": f"Bearer {self._key()}"})
        if r.status_code != 200:
            raise GatewayError("http_error", r.status_code)
        data = r.json()
        return int(data.get("input_tokens", data.get("total_tokens", 0)))

    def _log(self, model: str, t0: float, usd: float, error: str | None = None, reasoning: int | None = None,
             frames: int | None = None, audio_s: float | None = None) -> None:
        line = {"event": "model_call", "model": model, "latency_ms": round((time.monotonic() - t0) * 1000),
                "cost_usd": round(usd, 6)}
        self.metrics.add(model, line["latency_ms"], usd, error)
        if reasoning is not None:
            line["reasoning_tokens"] = reasoning
        if frames is not None:
            line["video_frames_processed"] = frames
        if audio_s is not None:
            line["audio_s"] = audio_s
        if error:
            line["error"] = error
        log.info(json.dumps(line))


gateway = Gateway()
