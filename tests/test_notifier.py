"""Notifier testleri: webhook gönderimi ve HTTP health probe."""

from __future__ import annotations

import threading

from procvigil.config import HealthCheck, Notify
from procvigil.notifier import Event, HealthMonitor, send_notification

from .conftest import wait_for


def _event(event: str = "crash") -> Event:
    return Event(
        program="demo",
        instance="demo:0",
        event=event,
        state="BACKOFF",
        hostname="testhost",
        pid=123,
        exitcode=1,
        message="patladi",
    )


def test_send_notification_posts_payload(mock_server):
    notify = Notify(url=mock_server.url + "/hook", events=["crash"])
    send_notification(notify, _event("crash"))

    assert wait_for(lambda: len(mock_server.requests) == 1)
    req = mock_server.requests[0]
    assert req["method"] == "POST"
    assert req["path"] == "/hook"
    assert req["json"]["program"] == "demo"
    assert req["json"]["event"] == "crash"
    assert req["json"]["exitcode"] == 1
    assert "timestamp" in req["json"]


def test_send_notification_skips_unsubscribed_event(mock_server):
    notify = Notify(url=mock_server.url, events=["fatal"])
    send_notification(notify, _event("crash"))
    # crash, events listesinde yok -> istek atılmamalı.
    assert wait_for(lambda: len(mock_server.requests) > 0, timeout=1.0) is False


def test_send_notification_sends_custom_headers(mock_server):
    notify = Notify(
        url=mock_server.url,
        events=["start"],
        headers={"X-Token": "secret"},
    )
    send_notification(notify, _event("start"))
    assert wait_for(lambda: len(mock_server.requests) == 1)
    assert mock_server.requests[0]["headers"].get("X-Token") == "secret"


def test_send_notification_retries_on_server_error(mock_server):
    mock_server.set_status(500)
    notify = Notify(url=mock_server.url, events=["crash"], retries=2, retry_backoff=0.05)
    send_notification(notify, _event("crash"))
    # 1 ilk deneme + 2 retry = 3 istek beklenir.
    assert wait_for(lambda: len(mock_server.requests) >= 3, timeout=3.0)


def test_health_monitor_triggers_on_unhealthy(mock_server):
    mock_server.set_status(503)  # her zaman sağlıksız
    triggered = threading.Event()
    check = HealthCheck(
        url=mock_server.url,
        interval=0.1,
        timeout=1.0,
        unhealthy_threshold=2,
    )
    monitor = HealthMonitor(
        program_name="demo",
        check=check,
        is_running=lambda: True,
        on_unhealthy_cb=triggered.set,
    )
    monitor.start()
    try:
        assert triggered.wait(timeout=5.0) is True
    finally:
        monitor.stop()


def test_health_monitor_healthy_does_not_trigger(mock_server):
    mock_server.set_status(200)
    triggered = threading.Event()
    check = HealthCheck(url=mock_server.url, interval=0.1, unhealthy_threshold=2)
    monitor = HealthMonitor(
        program_name="demo",
        check=check,
        is_running=lambda: True,
        on_unhealthy_cb=triggered.set,
    )
    monitor.start()
    try:
        assert triggered.wait(timeout=1.0) is False
    finally:
        monitor.stop()


def test_health_monitor_skips_when_not_running(mock_server):
    mock_server.set_status(503)
    triggered = threading.Event()
    check = HealthCheck(url=mock_server.url, interval=0.1, unhealthy_threshold=1)
    monitor = HealthMonitor(
        program_name="demo",
        check=check,
        is_running=lambda: False,  # süreç çalışmıyor -> probe yapılmamalı
        on_unhealthy_cb=triggered.set,
    )
    monitor.start()
    try:
        assert triggered.wait(timeout=1.0) is False
        assert len(mock_server.requests) == 0
    finally:
        monitor.stop()
