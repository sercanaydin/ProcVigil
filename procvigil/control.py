"""Kontrol soketi: pvctl <-> daemon iletişimi (supervisorctl muadili).

Daemon bir Unix domain socket dinler. İstemci (pvctl) tek satır JSON istek
gönderir, tek satır JSON yanıt alır.

İstek örneği:   {"cmd": "restart", "name": "laravel-queue"}
Yanıt örneği:   {"ok": true, "message": "laravel-queue: yeniden başlatıldı"}
"""

from __future__ import annotations

import json
import logging
import os
import socket
import threading

log = logging.getLogger("procvigil")

# Soket üzerinden okunacak maksimum istek boyutu (güvenlik için sınır).
_MAX_REQUEST = 64 * 1024


class ControlServer:
    """Daemon tarafında çalışan Unix socket kontrol sunucusu."""

    def __init__(self, supervisor, socket_path: str) -> None:
        self._sup = supervisor
        self._path = socket_path
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        directory = os.path.dirname(self._path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        # Eski/artık soket dosyasını temizle.
        if os.path.exists(self._path):
            os.unlink(self._path)

        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(self._path)
        self._sock.listen(8)
        self._sock.settimeout(0.5)
        # Soketin grubunu daemon'un efektif grubuna sabitle. macOS/BSD'de yeni
        # dosya, sürecin grubunu değil bulunduğu dizinin grubunu miras alır;
        # bu, plist'teki GroupName ile açılan sudo'suz erişimi bozardı. Linux'ta
        # ise zaten doğru grup (systemd Group=) korunur.
        try:
            os.chown(self._path, -1, os.getegid())
        except OSError:
            pass
        # Yalnızca sahip + grup erişebilsin (sertleştirme).
        os.chmod(self._path, 0o660)

        self._thread = threading.Thread(target=self._serve, name="control", daemon=True)
        self._thread.start()
        log.info("kontrol soketi dinliyor: %s", self._path)

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except (TimeoutError, socket.timeout):  # noqa: UP041
                # Python 3.9'da socket.timeout, TimeoutError'in alt sinifi degildir
                # (3.10'da birlestirildi). macOS sistem python3'u 3.9 olabildiginden
                # her ikisini de yakala ki accept dongusu ilk timeout'ta olmesin.
                continue
            except OSError:
                break
            with conn:
                try:
                    self._handle(conn)
                except Exception:  # noqa: BLE001
                    log.exception("kontrol isteği işlenirken hata")

    def _handle(self, conn: socket.socket) -> None:
        conn.settimeout(5.0)
        buf = b""
        while b"\n" not in buf and len(buf) < _MAX_REQUEST:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
        line = buf.split(b"\n", 1)[0].strip()
        if not line:
            return
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            self._reply(conn, {"ok": False, "message": "geçersiz JSON istek"})
            return
        response = self._dispatch(request)
        self._reply(conn, response)

    def _dispatch(self, request: dict) -> dict:
        cmd = str(request.get("cmd", "")).lower()
        name = request.get("name")

        if cmd == "status":
            return {"ok": True, "data": self._sup.status()}
        if cmd == "ping":
            return {"ok": True, "message": "pong"}
        if cmd == "reload":
            return {"ok": True, "message": self._sup.reload()}
        if cmd in ("start", "stop", "restart"):
            if not name:
                return {"ok": False, "message": f"'{cmd}' için program adı gerekli"}
            method = getattr(self._sup, f"{cmd}_program")
            ok, msg = method(name)
            return {"ok": ok, "message": msg}
        if cmd == "tail":
            if not name:
                return {"ok": False, "message": "'tail' için program adı gerekli"}
            stream = str(request.get("stream", "out"))
            lines = int(request.get("lines", 50))
            ok, msg = self._sup.tail(name, stream, lines)
            return {"ok": ok, "message": msg}
        return {"ok": False, "message": f"bilinmeyen komut: {cmd}"}

    @staticmethod
    def _reply(conn: socket.socket, payload: dict) -> None:
        data = (json.dumps(payload) + "\n").encode("utf-8")
        try:
            conn.sendall(data)
        except OSError:
            pass

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
        if os.path.exists(self._path):
            try:
                os.unlink(self._path)
            except OSError:
                pass
        log.info("kontrol soketi kapatıldı")


class ControlError(Exception):
    """İstemci tarafı kontrol hatası (bağlantı/iletişim)."""


def send_command(socket_path: str, request: dict, timeout: float = 5.0) -> dict:
    """pvctl istemcisi: sokete bağlanıp tek istek gönderir, yanıt döner."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(socket_path)
    except FileNotFoundError as exc:
        raise ControlError(
            f"Kontrol soketi bulunamadı: {socket_path}. ProcVigil daemon çalışıyor mu?"
        ) from exc
    except ConnectionRefusedError as exc:
        raise ControlError(f"Sokete bağlanılamadı: {socket_path}") from exc
    except OSError as exc:
        raise ControlError(f"Sokete bağlanılamadı ({socket_path}): {exc}") from exc

    try:
        sock.sendall((json.dumps(request) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf and len(buf) < _MAX_REQUEST:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
    finally:
        sock.close()

    line = buf.split(b"\n", 1)[0].strip()
    if not line:
        raise ControlError("Daemon'dan yanıt alınamadı")
    try:
        return json.loads(line)
    except json.JSONDecodeError as exc:
        raise ControlError("Daemon'dan geçersiz yanıt") from exc
