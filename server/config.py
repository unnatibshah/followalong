"""Config files in app/config/ (NFR-90). Read once at import; edit the TOML, the --reload server picks it up."""

import json
import os
import re
import tomllib
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent / "config"


def _load(name: str) -> dict:
    return tomllib.loads((DIR / f"{name}.toml").read_text(encoding="utf-8"))


def _prompt(name: str) -> dict:
    """config/prompts/<name>.md: a `prompt_version:` line, then `## system` and `## user` sections."""
    text = (DIR / "prompts" / f"{name}.md").read_text(encoding="utf-8")
    parts = dict(re.findall(r"^## (\w+)\n(.*?)(?=^## |\Z)", text, re.S | re.M))
    return {"version": re.search(r"^prompt_version: *(\S+)", text, re.M)[1],
            "system": parts["system"].strip(), "user": parts["user"].strip(),
            "transcript": parts.get("transcript", "").strip() or None}


MODELS, TIMEOUTS, PRICES, CHECKS, MOTION = (_load(n) for n in ("models", "timeouts", "prices", "checks", "motion"))
# Any server that speaks the Meta Model API (a proxy, or the local stand-in in tests/e2e/manual/standin_meta.py).
MODELS["base_url"] = os.environ.get("FA_API_BASE") or MODELS["base_url"]


def check_models(models: dict) -> None:
    """FR-153 at startup: every configured model is on the allow-list (the gateway refuses again on every call)."""
    bad = sorted({models[k] for k in ("segment", "judged", "plan", "voice")
                  if models[k] not in models["allowed"] or "-contributor" in models[k]})
    if bad:
        raise SystemExit(f"config/models.toml: {', '.join(bad)} isn't on the allow-list (FR-153).")


check_models(MODELS)
JUDGED_PROMPT = _prompt("judged_checks")
JUDGED_SCHEMA = json.loads((DIR / "schemas" / "judged_checks.json").read_text(encoding="utf-8"))
PLAN_PROMPT = _prompt("step_plan")
QUESTION_PROMPT = _prompt("question")
COACH_PROMPT = _prompt("coach")
WATCH_PROMPT = _prompt("watch")
PLAN_SCHEMA = json.loads((DIR / "schemas" / "step_plan.json").read_text(encoding="utf-8"))

APP = DIR.parent
DATA = APP / "data"  # bundled media (committed): tools/make_stand_in_videos.py
# Live state: session logs, budget.json, visitors.json, uploads, plan edits, new recordings. Gitignored by default.
RUNTIME = Path(os.environ.get("FA_DATA_DIR") or DATA / "runtime")

_env = os.environ.get
HOSTED = _env("FA_HOSTED", "false").lower() == "true"
TEST_MODE = _env("FA_TEST_MODE", "false").lower() == "true"
PROVIDER = _env("FA_PROVIDER", "live")
CACHED_RECORDING = _env("FA_CACHED_RECORDING") or None
CAPS = {"session": float(_env("FA_CAP_SESSION_USD", 1.50)), "day": float(_env("FA_CAP_DAY_USD", 10)),
        "total": float(_env("FA_CAP_TOTAL_USD", 50))}
LIMITS = {"sessions_per_day": int(_env("FA_VISITOR_SESSIONS_PER_DAY", 2)),
          "uploads_per_day": int(_env("FA_VISITOR_UPLOADS_PER_DAY", 1)),
          # per client IP: a cookie-less script gets no more; a shared office or school network still fits
          "ip_sessions_per_day": int(_env("FA_IP_SESSIONS_PER_DAY", 10)),
          "concurrent": int(_env("FA_MAX_CONCURRENT_SESSIONS", 2)),
          "idle_s": float(_env("FA_SESSION_IDLE_MINUTES", 10)) * 60,
          # a heartbeat keeps a session from going idle, so a slot is also freed after this long (cookie-less scripts)
          "max_age_s": float(_env("FA_SESSION_MAX_MINUTES", 30)) * 60}
UPLOAD = {"max_s": int(_env("FA_UPLOAD_MAX_SECONDS", 180 if HOSTED else 600)),
          "max_mb": int(_env("FA_UPLOAD_MAX_MB", 100 if HOSTED else 200)),
          "ttl_h": float(_env("FA_UPLOAD_TTL_HOURS", 24)),
          "convert": _env("FA_CONVERT_VIDEO", "true").lower() == "true",
          # processed at once (ffmpeg + Muse Spark + SAM; ~0.5 CPU and 512 MB on Render starter), and the most
          # running or waiting their turn before a new upload is refused
          "concurrent": int(_env("FA_MAX_CONCURRENT_UPLOADS", 2)),
          "jobs": int(_env("FA_MAX_UPLOAD_JOBS", 6))}
# TD §3: the hand request in parallel with the object requests. Breaks NFR-13 (hand gate first), so it needs a
# change request; main.py logs a warning at startup when it's on.
HAND_GATE_PARALLEL = _env("FA_HAND_GATE_PARALLEL", "false").lower() == "true"
# Same-origin rule for state-changing requests and the voice WebSocket: this origin when set (the hosted demo behind
# a proxy, e.g. https://<app>.onrender.com), else the request's own Host.
# Paste a link (YouTube/Vimeo) instead of a file: on locally, off on the hosted demo unless set (platform terms, SSRF)
LINK_IMPORT = _env("FA_LINK_IMPORT", "false" if HOSTED else "true").lower() == "true"
LINK_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "vimeo.com", "www.vimeo.com", "player.vimeo.com")
ALLOWED_ORIGIN = (_env("FA_ALLOWED_ORIGIN") or _env("RENDER_EXTERNAL_URL") or "").rstrip("/") or None  # Render sets the latter
# Voice (TD §4.6): FA_VOICE asks for realtime, push_to_talk or off; /api/config says what's actually offered.
VOICE = {"mode": _env("FA_VOICE", "realtime"), "max_s": float(_env("FA_VOICE_MAX_SECONDS", 600)),
         "idle_s": float(_env("FA_WS_IDLE_SECONDS", 60))}

if PROVIDER not in ("live", "recording", "cached"):
    raise SystemExit(f"FA_PROVIDER={PROVIDER!r}: use live, recording or cached.")
if VOICE["mode"] not in ("realtime", "push_to_talk", "off"):
    raise SystemExit(f"FA_VOICE={VOICE['mode']!r}: use realtime, push_to_talk or off.")
if PROVIDER == "recording" and HOSTED:
    raise SystemExit("FA_PROVIDER=recording is refused when FA_HOSTED=true: frames never land on the hosted disk (NFR-40).")
if TEST_MODE and (_env("MODEL_API_KEY") or _env("RENDER")):
    raise SystemExit("FA_TEST_MODE=true is refused with MODEL_API_KEY set or on Render.")
if CACHED_RECORDING and HOSTED and not TEST_MODE:
    raise SystemExit("FA_CACHED_RECORDING is refused when FA_HOSTED=true (unless FA_TEST_MODE=true, for SIT).")
