import glob
import os
import socket
import subprocess
import sys
import time

import httpx
import pytest


def _chromium():
    for pat in ("/opt/pw-browsers/chromium-*/chrome-linux*/chrome", "/opt/pw-browsers/chromium/chrome-linux*/chrome",
                "/opt/pw-browsers/chromium"):
        hits = [h for h in glob.glob(pat) if os.path.isfile(h)]
        if hits:
            return hits[0]
    return None


@pytest.fixture(scope="session")
def server(tmp_path_factory):
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    db = tmp_path_factory.mktemp("e2e") / "e2e.db"
    env = {**os.environ, "ENV": "test", "PROVIDER": "fake", "DATABASE_URL": f"sqlite:///{db}", "EMBEDDED_WORKER": "true",
           "WORKER_CONCURRENCY": "2", "POLL_INTERVAL_S": "0.2"}
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--log-level", "warning"], env=env)
    url = f"http://127.0.0.1:{port}"
    for _ in range(80):
        try:
            if httpx.get(url + "/health/ready").status_code == 200:
                break
        except Exception:
            time.sleep(0.25)
    yield url
    p.terminate(); p.wait(timeout=10)


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        exe = _chromium()
        b = pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
        yield b
        b.close()
