"""Kontrol soketi (pvctl) testleri."""

from __future__ import annotations

import os
import textwrap
import uuid

import pytest

from procvigil.config import load_config
from procvigil.control import ControlError, ControlServer, send_command
from procvigil.process import State
from procvigil.supervisor import Supervisor

from .conftest import wait_for


def _short_socket() -> str:
    # macOS'ta AF_UNIX yol uzunluğu ~104 karakterle sınırlı; kısa /tmp yolu kullan.
    return f"/tmp/nrtest-{uuid.uuid4().hex[:8]}.sock"


@pytest.fixture
def running_supervisor(tmp_path):
    """Kontrol soketi açık, iki programlı çalışan bir supervisor."""
    logdir = tmp_path / "logs"
    sock = _short_socket()
    body = f"""
    procvigil:
      logdir: {logdir}
      socket: {sock}
    programs:
      - name: alpha
        command: sleep 30
        startsecs: 0.3
      - name: beta
        command: sleep 30
        startsecs: 0.3
    """
    cfg_path = tmp_path / "procvigil.yml"
    cfg_path.write_text(textwrap.dedent(body), encoding="utf-8")
    cfg = load_config(str(cfg_path))
    sup = Supervisor(str(cfg_path), cfg)
    sup.start()
    control = ControlServer(sup, cfg.socket_path)
    control.start()
    try:
        yield sup, cfg.socket_path
    finally:
        control.stop()
        sup._do_shutdown()


def _ok(sock, **req):
    resp = send_command(sock, req)
    return resp


def test_ping(running_supervisor):
    _, sock = running_supervisor
    resp = _ok(sock, cmd="ping")
    assert resp["ok"] is True
    assert resp["message"] == "pong"


def test_status_lists_instances(running_supervisor):
    sup, sock = running_supervisor
    assert wait_for(lambda: all(r["state"] == State.RUNNING.value for r in sup.status()), timeout=6.0)
    resp = _ok(sock, cmd="status")
    assert resp["ok"] is True
    names = {r["name"] for r in resp["data"]}
    assert names == {"alpha", "beta"}


def test_stop_then_start_program(running_supervisor):
    sup, sock = running_supervisor

    def state(name):
        return next(r["state"] for r in sup.status() if r["name"] == name)

    assert wait_for(lambda: state("alpha") == State.RUNNING.value, timeout=6.0)

    resp = _ok(sock, cmd="stop", name="alpha")
    assert resp["ok"] is True
    assert state("alpha") == State.STOPPED.value
    # beta etkilenmemeli
    assert state("beta") == State.RUNNING.value

    resp = _ok(sock, cmd="start", name="alpha")
    assert resp["ok"] is True
    assert wait_for(lambda: state("alpha") == State.RUNNING.value, timeout=6.0)


def test_restart_program(running_supervisor):
    sup, sock = running_supervisor

    def alpha():
        return next(r for r in sup.status() if r["name"] == "alpha")

    assert wait_for(lambda: alpha()["state"] == State.RUNNING.value, timeout=6.0)
    starts_before = alpha()["starts"]

    resp = _ok(sock, cmd="restart", name="alpha")
    assert resp["ok"] is True
    assert wait_for(lambda: alpha()["state"] == State.RUNNING.value, timeout=6.0)
    assert wait_for(lambda: alpha()["starts"] > starts_before, timeout=6.0)


def test_unknown_program(running_supervisor):
    _, sock = running_supervisor
    resp = _ok(sock, cmd="stop", name="ghost")
    assert resp["ok"] is False
    assert "ghost" in resp["message"]


def test_unknown_command(running_supervisor):
    _, sock = running_supervisor
    resp = _ok(sock, cmd="frobnicate")
    assert resp["ok"] is False


def test_reload_via_control(running_supervisor):
    _, sock = running_supervisor
    resp = _ok(sock, cmd="reload")
    assert resp["ok"] is True
    assert "reload" in resp["message"].lower()


def test_connect_to_missing_socket_raises():
    path = _short_socket()
    assert not os.path.exists(path)
    with pytest.raises(ControlError):
        send_command(path, {"cmd": "ping"})
