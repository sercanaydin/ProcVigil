"""Metrics HTTP endpoint testleri."""

from __future__ import annotations

import json
import textwrap
import urllib.error
import urllib.request

import pytest

from procvigil.config import MetricsConfig, load_config
from procvigil.metrics import MetricsServer, render_prometheus
from procvigil.process import State
from procvigil.supervisor import Supervisor

from .conftest import wait_for


def _http_get(url: str, timeout: float = 3.0) -> tuple[int, str]:
    """stdlib ile GET; (status_code, body) döner. 4xx/5xx için de durum kodu."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def test_render_prometheus_format():
    rows = [
        {"name": "w:0", "program": "w", "state": "RUNNING", "pid": 10, "starts": 1, "exitcode": None},
        {"name": "w:1", "program": "w", "state": "BACKOFF", "pid": None, "starts": 3, "exitcode": 1},
    ]
    out = render_prometheus(rows)
    assert "procvigil_up 1" in out
    assert 'procvigil_instance_running{program="w",instance="w:0"} 1' in out
    assert 'procvigil_instance_running{program="w",instance="w:1"} 0' in out
    assert 'procvigil_instance_starts_total{program="w",instance="w:1"} 3' in out
    assert 'state="BACKOFF"' in out


@pytest.fixture
def metrics_supervisor(tmp_path):
    logdir = tmp_path / "logs"
    body = f"""
    procvigil:
      logdir: {logdir}
    programs:
      - name: svc
        command: sleep 30
        startsecs: 0.3
    """
    cfg_path = tmp_path / "procvigil.yml"
    cfg_path.write_text(textwrap.dedent(body), encoding="utf-8")
    cfg = load_config(str(cfg_path))
    sup = Supervisor(str(cfg_path), cfg)
    sup.start()
    # port 0 -> OS boş port atar
    server = MetricsServer(sup, MetricsConfig(enabled=True, host="127.0.0.1", port=0))
    server.start()
    port = server._httpd.server_address[1]
    try:
        yield sup, port
    finally:
        server.stop()
        sup._do_shutdown()


def test_metrics_endpoint(metrics_supervisor):
    sup, port = metrics_supervisor
    assert wait_for(lambda: any(r["state"] == State.RUNNING.value for r in sup.status()), timeout=6.0)
    status, text = _http_get(f"http://127.0.0.1:{port}/metrics")
    assert status == 200
    assert "procvigil_up 1" in text
    assert 'instance="svc"' in text


def test_status_endpoint(metrics_supervisor):
    sup, port = metrics_supervisor
    status, text = _http_get(f"http://127.0.0.1:{port}/status")
    assert status == 200
    data = json.loads(text)
    assert data["up"] is True
    assert any(r["name"] == "svc" for r in data["instances"])


def test_unknown_path_404(metrics_supervisor):
    _, port = metrics_supervisor
    status, _text = _http_get(f"http://127.0.0.1:{port}/nope")
    assert status == 404
