"""HTTP bildirimleri (webhook) ve HTTP health-check.

Bildirim (Notify): bir program durumu değiştiğinde (start/crash/restart/...)
config'te belirtilen URL'ye -> "belirlediğimiz parametrelere o an istek atma"
mantığıyla HTTP isteği atar. İstek arka planda, daemon'u bloklamadan gönderilir.

HealthCheck: bir programın canlılığını PID dışında HTTP üzerinden de doğrular;
ardışık başarısızlık eşiği aşılırsa süreç yeniden başlatılabilir.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .config import HealthCheck, Notify

log = logging.getLogger("procvigil")


def _http_request(
    method: str,
    url: str,
    timeout: float,
    headers: dict[str, str] | None = None,
    json_body: dict | None = None,
) -> int:
    """stdlib urllib ile HTTP isteği atar, HTTP durum kodunu döner.

    Bağlantı hatası gibi durumlarda urllib.error.URLError fırlatır.
    """
    # Yalnızca http/https'e izin ver: file:// gibi şemalarla yerel dosya
    # okunmasını/ beklenmedik şema kullanımını engelle (CWE-22 sertleştirmesi).
    scheme = urllib.parse.urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise urllib.error.URLError(f"izin verilmeyen URL şeması: {scheme or '(yok)'}")

    data = None
    req_headers = dict(headers or {})
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url, data=data, headers=req_headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status
    except urllib.error.HTTPError as exc:
        # 4xx/5xx de geçerli bir HTTP yanıtıdır; durum kodunu döndür.
        return exc.code


@dataclass
class Event:
    """Webhook payload'ı olarak gönderilecek olay bilgisi."""

    program: str
    instance: str
    event: str
    state: str
    hostname: str
    pid: int | None = None
    exitcode: int | None = None
    message: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "program": self.program,
            "instance": self.instance,
            "event": self.event,
            "state": self.state,
            "hostname": self.hostname,
            "pid": self.pid,
            "exitcode": self.exitcode,
            "message": self.message,
            "timestamp": time.time(),
        }


def send_notification(notify: Notify, event: Event) -> None:
    """Webhook isteğini arka planda (ayrı thread) gönderir."""
    if event.event not in notify.events:
        return

    def _worker() -> None:
        payload = event.to_payload()
        attempt = 0
        while attempt <= notify.retries:
            try:
                status = _http_request(
                    method=notify.method,
                    url=notify.url,
                    timeout=notify.timeout,
                    headers=notify.headers,
                    json_body=payload,
                )
                if status < 400:
                    log.debug(
                        "notify ok program=%s event=%s status=%s",
                        event.program,
                        event.event,
                        status,
                    )
                    return
                log.warning(
                    "notify başarısız program=%s event=%s status=%s (deneme %d/%d)",
                    event.program,
                    event.event,
                    status,
                    attempt + 1,
                    notify.retries + 1,
                )
            except (urllib.error.URLError, OSError) as exc:
                log.warning(
                    "notify hata program=%s event=%s err=%s (deneme %d/%d)",
                    event.program,
                    event.event,
                    exc,
                    attempt + 1,
                    notify.retries + 1,
                )
            attempt += 1
            if attempt <= notify.retries:
                time.sleep(notify.retry_backoff * attempt)
        log.error(
            "notify tamamen başarısız program=%s event=%s url=%s",
            event.program,
            event.event,
            notify.url,
        )

    threading.Thread(target=_worker, name=f"notify-{event.program}", daemon=True).start()


class HealthMonitor:
    """Bir program için periyodik HTTP health-check çalıştıran thread.

    ``is_running`` ile sürecin çalışıp çalışmadığı sorgulanır; sadece RUNNING
    durumdayken denetim yapılır. Eşik aşılırsa ``on_unhealthy_cb`` çağrılır.
    """

    def __init__(
        self,
        program_name: str,
        check: HealthCheck,
        is_running: Callable[[], bool],
        on_unhealthy_cb: Callable[[], None],
    ) -> None:
        self._name = program_name
        self._check = check
        self._is_running = is_running
        self._on_unhealthy = on_unhealthy_cb
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, name=f"health-{program_name}", daemon=True
        )
        self._failures = 0

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _probe(self) -> bool:
        try:
            status = _http_request(
                method=self._check.method,
                url=self._check.url,
                timeout=self._check.timeout,
            )
            return status in self._check.expect_status
        except (urllib.error.URLError, OSError):
            return False

    def _loop(self) -> None:
        while not self._stop.wait(self._check.interval):
            if not self._is_running():
                # Süreç çalışmıyorsa sayacı sıfırla; gözcü zaten ilgilenir.
                self._failures = 0
                continue
            if self._probe():
                if self._failures:
                    log.info("health iyileşti program=%s", self._name)
                self._failures = 0
                continue

            self._failures += 1
            log.warning(
                "health başarısız program=%s (%d/%d)",
                self._name,
                self._failures,
                self._check.unhealthy_threshold,
            )
            if self._failures >= self._check.unhealthy_threshold:
                log.error("health eşiği aşıldı program=%s -> on_unhealthy", self._name)
                self._failures = 0
                try:
                    self._on_unhealthy()
                except Exception:  # noqa: BLE001 - callback hatası daemon'u düşürmesin
                    log.exception("on_unhealthy callback hata verdi program=%s", self._name)
