"""Every main screen renders in a real browser with no page error (a blank page once shipped: a button used before it
was defined). Skipped where Playwright's Chromium isn't installed."""

import socket
import threading
import time

import pytest

playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def base():
    import uvicorn

    from server.main import app
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


def test_main_screens_render_without_errors(base):
    try:
        pw = playwright.sync_playwright().start()
        browser = pw.chromium.launch()
    except Exception as e:  # noqa: BLE001 no browser binary here
        pytest.skip(f"no Chromium: {e}")
    try:
        for route, text in [("#/", "Pick a tutorial"), ("#/replay", "Choose a session"), ("#/evaluation", ""),
                            ("#/privacy", "Privacy"), ("#/ops", "Operations")]:
            page, errors = browser.new_page(), []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{base}/{route}", wait_until="networkidle")
            page.wait_for_timeout(300)
            body = page.evaluate("document.querySelector('main').innerText")
            assert not errors, f"{route}: {errors}"
            assert len(body) > 50 and text in body, f"{route} rendered nothing useful"
            page.close()
    finally:
        browser.close()
        pw.stop()
