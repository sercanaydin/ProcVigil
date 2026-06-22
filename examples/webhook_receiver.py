#!/usr/bin/env python3
"""Basit webhook alıcı — ProcVigil'ın HTTP bildirimlerini görmek için.

ProcVigil bir olay (start/crash/restart/...) gerçekleştiğinde config'teki
``notify.url`` adresine HTTP isteği atar. Bu küçük sunucu o istekleri yakalayıp
terminale yazdırır. Ayrıca ``/health`` yolunda 200 döner, böylece health-check
örneğini de besler.

Çalıştırma:
    python3 examples/webhook_receiver.py            # 127.0.0.1:9000
    python3 examples/webhook_receiver.py --port 9001
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Health-check örneği için: /health daima 200 döner.
        if self.path == "/health":
            self._respond(200, {"status": "ok"})
            return
        self._respond(200, {"hint": "POST bekleniyor (webhook)"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        ts = datetime.now().strftime("%H:%M:%S")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {"_raw": raw.decode("utf-8", "replace")}

        event = data.get("event", "?")
        program = data.get("program", "?")
        state = data.get("state", "?")
        exitcode = data.get("exitcode")
        message = data.get("message", "")
        print(
            f"[{ts}] WEBHOOK  program={program:<16} event={event:<8} "
            f"state={state:<8} exit={exitcode}  {message}",
            flush=True,
        )
        self._respond(200, {"received": True})

    def _respond(self, status: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # HTTP server'ın varsayılan gürültüsünü sustur
        pass


def main():
    parser = argparse.ArgumentParser(description="ProcVigil webhook alıcı (demo).")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Webhook alıcı dinliyor: http://{args.host}:{args.port}")
    print(f"  - Webhook URL'si  : http://{args.host}:{args.port}/hook")
    print(f"  - Health-check URL: http://{args.host}:{args.port}/health")
    print("Çıkmak için Ctrl+C\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nKapatılıyor...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
