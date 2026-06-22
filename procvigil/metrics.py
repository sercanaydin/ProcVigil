"""Observability HTTP sunucusu: /status (JSON) ve /metrics (Prometheus).

Opsiyoneldir; config'te ``procvigil.metrics.enabled: true`` ile açılır. İzleme
sistemlerinin (Prometheus) ve operatörlerin daemon durumunu çekebilmesi için.
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import MetricsConfig

log = logging.getLogger("procvigil")


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def render_prometheus(rows: list[dict]) -> str:
    """status() satırlarını Prometheus text formatına çevirir."""
    lines: list[str] = []
    lines.append("# HELP procvigil_up ProcVigil daemon çalışıyor.")
    lines.append("# TYPE procvigil_up gauge")
    lines.append("procvigil_up 1")

    lines.append("# HELP procvigil_instance_running Instance RUNNING durumunda mı (1/0).")
    lines.append("# TYPE procvigil_instance_running gauge")
    for r in rows:
        labels = f'program="{_escape(r["program"])}",instance="{_escape(r["name"])}"'
        running = 1 if r["state"] == "RUNNING" else 0
        lines.append(f"procvigil_instance_running{{{labels}}} {running}")

    lines.append("# HELP procvigil_instance_starts_total Instance kaç kez başlatıldı.")
    lines.append("# TYPE procvigil_instance_starts_total counter")
    for r in rows:
        labels = f'program="{_escape(r["program"])}",instance="{_escape(r["name"])}"'
        lines.append(f"procvigil_instance_starts_total{{{labels}}} {r['starts']}")

    lines.append("# HELP procvigil_instance_state_info Instance durumu (label olarak).")
    lines.append("# TYPE procvigil_instance_state_info gauge")
    for r in rows:
        labels = (
            f'program="{_escape(r["program"])}",'
            f'instance="{_escape(r["name"])}",state="{_escape(r["state"])}"'
        )
        lines.append(f"procvigil_instance_state_info{{{labels}}} 1")

    return "\n".join(lines) + "\n"


class MetricsServer:
    def __init__(self, supervisor, config: MetricsConfig) -> None:
        self._sup = supervisor
        self._config = config
        sup = supervisor

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if self.path.rstrip("/") in ("/metrics", ""):
                    if self.path.rstrip("/") == "":
                        self._send(200, "text/plain", "ProcVigil: /status, /metrics\n")
                        return
                    body = render_prometheus(sup.status())
                    self._send(200, "text/plain; version=0.0.4", body)
                elif self.path.rstrip("/") == "/status":
                    body = json.dumps({"up": True, "instances": sup.status()}, indent=2)
                    self._send(200, "application/json", body)
                else:
                    self._send(404, "text/plain", "not found\n")

            def _send(self, status: int, ctype: str, body: str):
                data = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):  # daemon logunu kirletmesin
                pass

        self._httpd = ThreadingHTTPServer((config.host, config.port), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="metrics", daemon=True)

    def start(self) -> None:
        self._thread.start()
        log.info("metrics sunucusu dinliyor: http://%s:%d", self._config.host, self._config.port)

    def stop(self) -> None:
        try:
            self._httpd.shutdown()
            self._httpd.server_close()
        except Exception:  # noqa: BLE001
            pass
        log.info("metrics sunucusu kapatıldı")
