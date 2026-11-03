"""REG-05 (NFR-22): both bundled recordings replay with the verdict sequence they recorded, with no call to Meta.
Run alone: .venv/bin/python -m pytest tests/test_replay.py"""

import json

import pytest
from fastapi.testclient import TestClient

from server import providers
from server.main import app

from test_api import check


@pytest.mark.parametrize("rid", ["clean", "with-mistakes"])
def test_reg05_replay_matches_the_recorded_verdicts(rid, monkeypatch):
    async def no_meta(*a, **kw):
        raise AssertionError("a replay called Meta")
    monkeypatch.setattr(providers.gateway, "_post", no_meta)
    c = TestClient(app)
    events = [json.loads(line) for line in c.get(f"/api/recordings/{rid}/timeline").text.splitlines()]
    sid = c.post("/api/sessions", json={"tutorial_id": "install-thermostat", "mode": "replay",
                                        "recording_id": rid}).json()["session_id"]
    checks = [e for e in events if e["type"] in ("check", "recheck")]
    got = [check(c, sid, e["step_id"], e["data"]["check_no"], e["data"]["kind"]).json()["verdict"] for e in checks]
    assert got == [e["data"]["verdict"] for e in checks] and len(got) > 10
