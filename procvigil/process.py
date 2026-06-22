"""Tek bir gözetilen süreç instance'ı.

Her ``ProcessInstance`` kendi monitör thread'inde çalışır ve supervisor ile
aynı durum makinesini uygular:

    STOPPED  -> hiç başlatılmadı / durduruldu
    STARTING -> başlatıldı, RUNNING sayılmak için ``startsecs`` bekleniyor
    RUNNING  -> stabil, ayakta
    BACKOFF  -> startsecs dolmadan öldü, yeniden denenecek
    STOPPING -> durdurma istendi, çıkış bekleniyor
    EXITED   -> RUNNING iken çıktı
    FATAL    -> startretries tükendi, vazgeçildi

Felsefe: süreç ideal olarak hiç ölmemeli. ProcVigil "şu an canlı mı?" sorusuyla
ilgilenir; ölürse saniyesinde yeniden kaldırır.
"""

from __future__ import annotations

import logging
import os
import pwd
import signal
import subprocess
import threading
import time
from enum import Enum

from .config import ProgramConfig
from .logger import make_program_logger
from .notifier import Event, send_notification

log = logging.getLogger("procvigil")


class State(str, Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    BACKOFF = "BACKOFF"
    STOPPING = "STOPPING"
    EXITED = "EXITED"
    FATAL = "FATAL"


def _resolve_user(username: str | None):
    """Kullanıcı adından (uid, gid, home, groups) çözer; None ise değişiklik yok.

    ``groups``, kullanıcının birincil + yardımcı gruplarının tam listesidir.
    Bu liste ``extra_groups`` olarak verilir ki süreç root'un yardımcı gruplarını
    miras almasın (eksik ayrıcalık düşürmeyi önler).
    """
    if not username:
        return None
    try:
        record = pwd.getpwnam(username)
    except KeyError as exc:
        raise ValueError(f"Kullanıcı bulunamadı: {username}") from exc
    try:
        groups = os.getgrouplist(username, record.pw_gid)
    except OSError:
        groups = [record.pw_gid]
    return record.pw_uid, record.pw_gid, record.pw_dir, groups


class ProcessInstance:
    def __init__(
        self,
        program: ProgramConfig,
        index: int,
        hostname: str,
        logdir: str,
    ) -> None:
        self.program = program
        self.index = index
        self.hostname = hostname
        self.logdir = logdir
        self.name = program.name if program.numprocs == 1 else f"{program.name}:{index}"

        self.state: State = State.STOPPED
        self.pid: int | None = None
        self.exitcode: int | None = None
        self.start_count = 0

        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._shutdown = threading.Event()  # daemon kapanıyor
        self._stop_requested = threading.Event()  # bu instance durdurulsun
        self._lock = threading.Lock()

        self._stdout_log: logging.Logger | None = None
        self._stderr_log: logging.Logger | None = None
        self._readers: list[threading.Thread] = []

    # ------------------------------------------------------------------ utils
    def is_running(self) -> bool:
        return self.state in (State.STARTING, State.RUNNING)

    def _set_state(self, state: State) -> None:
        if self.state != state:
            log.info("durum program=%s %s -> %s", self.name, self.state.value, state.value)
        self.state = state

    def _emit(self, event: str, message: str | None = None) -> None:
        if not self.program.notify:
            return
        send_notification(
            self.program.notify,
            Event(
                program=self.program.name,
                instance=self.name,
                event=event,
                state=self.state.value,
                hostname=self.hostname,
                pid=self.pid,
                exitcode=self.exitcode,
                message=message,
            ),
        )

    def logfile_path(self, stream: str = "out") -> str:
        """Bu instance'ın stdout ('out') veya stderr ('err') log dosyası yolu."""
        return self._logfile_path(stream)

    def _logfile_path(self, stream: str) -> str:
        configured = (
            self.program.stdout_logfile if stream == "out" else self.program.stderr_logfile
        )
        if configured:
            if self.program.numprocs > 1:
                base, _, ext = configured.rpartition(".")
                if base:
                    return f"{base}.{self.index}.{ext}"
                return f"{configured}.{self.index}"
            return configured
        return os.path.join(self.logdir, f"{self.name.replace(':', '_')}.{stream}.log")

    def _ensure_loggers(self) -> None:
        if self._stdout_log is None:
            self._stdout_log = make_program_logger(
                f"{self.name}.out",
                self._logfile_path("out"),
                self.program.logfile_maxbytes,
                self.program.logfile_backups,
            )
        if self._stderr_log is None:
            self._stderr_log = make_program_logger(
                f"{self.name}.err",
                self._logfile_path("err"),
                self.program.logfile_maxbytes,
                self.program.logfile_backups,
            )

    def _pump(self, pipe, logger: logging.Logger) -> None:
        """Süreç çıktısını satır satır okuyup log dosyasına aktarır."""
        try:
            for line in iter(pipe.readline, ""):
                if line == "":
                    break
                logger.info(line.rstrip("\n"))
        except (ValueError, OSError):
            pass
        finally:
            try:
                pipe.close()
            except OSError:
                pass

    # ------------------------------------------------------------------ launch
    def _build_popen_kwargs(self) -> dict:
        kwargs: dict = {
            "args": self.program.argv(),
            "cwd": self.program.directory or None,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "stdin": subprocess.DEVNULL,
            "text": True,
            "bufsize": 1,
            # Kendi süreç grubunda başlat ki tüm alt süreçleri birlikte durdurabilelim.
            "start_new_session": True,
        }

        env = os.environ.copy()
        env.update(self.program.environment)

        resolved = _resolve_user(self.program.user)
        if resolved:
            uid, gid, home, groups = resolved
            kwargs["user"] = uid
            kwargs["group"] = gid
            # Root'un yardımcı gruplarını sızdırmamak için hedef kullanıcının
            # gruplarını açıkça ata (initgroups eşdeğeri).
            kwargs["extra_groups"] = groups
            env.setdefault("HOME", home)
            env.setdefault("USER", self.program.user or "")
        kwargs["env"] = env
        return kwargs

    def _spawn(self) -> bool:
        self._ensure_loggers()
        try:
            self._proc = subprocess.Popen(**self._build_popen_kwargs())  # noqa: S603
        except (OSError, ValueError) as exc:
            log.error("başlatılamadı program=%s err=%s", self.name, exc)
            self._proc = None
            return False

        self.pid = self._proc.pid
        self.start_count += 1
        log.info("başladı program=%s pid=%s (deneme #%d)", self.name, self.pid, self.start_count)

        self._readers = []
        for pipe, logger in (
            (self._proc.stdout, self._stdout_log),
            (self._proc.stderr, self._stderr_log),
        ):
            if pipe is not None and logger is not None:
                t = threading.Thread(
                    target=self._pump, args=(pipe, logger), daemon=True
                )
                t.start()
                self._readers.append(t)
        return True

    # ------------------------------------------------------------------ stop
    def _signal_proc(self, sig: signal.Signals) -> None:
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        try:
            # Tüm süreç grubuna gönder (start_new_session sayesinde pgid == pid).
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass
        except PermissionError:
            try:
                proc.send_signal(sig)
            except ProcessLookupError:
                pass

    def _terminate(self) -> None:
        """stopsignal gönder, stopwaitsecs bekle, gerekirse SIGKILL."""
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        sig = getattr(signal, f"SIG{self.program.stopsignal}", signal.SIGTERM)
        log.info("durduruluyor program=%s pid=%s signal=%s", self.name, proc.pid, sig.name)
        self._signal_proc(sig)

        deadline = time.monotonic() + self.program.stopwaitsecs
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return
            time.sleep(0.1)

        log.warning("zaman aşımı program=%s -> SIGKILL", self.name)
        self._signal_proc(signal.SIGKILL)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            log.error("SIGKILL sonrası bile kapanmadı program=%s pid=%s", self.name, proc.pid)

    # ------------------------------------------------------------------ policy
    def _should_restart(self, exitcode: int | None) -> bool:
        policy = self.program.autorestart
        if policy == "never":
            return False
        if policy == "always":
            return True
        # unexpected: sadece beklenmeyen exit kodunda yeniden başlat.
        return exitcode not in self.program.exitcodes

    def _backoff_delay(self, attempt: int) -> float:
        delay = self.program.backoff_base * (2 ** max(0, attempt - 1))
        return min(delay, self.program.backoff_max)

    # ------------------------------------------------------------------ loop
    def _monitor(self) -> None:
        retries = 0
        first = True
        while not self._shutdown.is_set() and not self._stop_requested.is_set():
            self._set_state(State.STARTING)
            if not self._spawn():
                retries += 1
                if retries >= self.program.startretries:
                    self._set_state(State.FATAL)
                    self._emit("fatal", "süreç başlatılamadı (exec hatası)")
                    return
                self._set_state(State.BACKOFF)
                if self._sleep_interruptible(self._backoff_delay(retries)):
                    break
                continue

            started_at = time.monotonic()
            became_running = False
            # startsecs boyunca ayakta kalırsa RUNNING say.
            while True:
                if self._proc is None:
                    break
                ret = self._proc.poll()
                if ret is not None:
                    self.exitcode = ret
                    break
                if not became_running and (time.monotonic() - started_at) >= self.program.startsecs:
                    became_running = True
                    retries = 0
                    self._set_state(State.RUNNING)
                    self._emit("restart" if not first else "start")
                    first = False
                if self._stop_requested.is_set() or self._shutdown.is_set():
                    break
                time.sleep(0.2)

            # Çıkış nedeni: durdurma isteği mi yoksa kendi kendine mi öldü?
            if self._stop_requested.is_set() or self._shutdown.is_set():
                self._set_state(State.STOPPING)
                self._terminate()
                self.pid = None
                self._set_state(State.STOPPED)
                return

            # Süreç kendiliğinden çıktı.
            self._join_readers()
            ec = self.exitcode
            log.info("çıktı program=%s pid=%s exitcode=%s", self.name, self.pid, ec)

            if not became_running:
                # startsecs dolmadan öldü -> başlatma denemesi sayılır.
                retries += 1
                self._set_state(State.BACKOFF)
                self._emit("crash", f"startsecs dolmadan çıktı (exitcode={ec})")
                if retries >= self.program.startretries:
                    self._set_state(State.FATAL)
                    self._emit("fatal", f"{self.program.startretries} denemede ayakta kalamadı")
                    log.error("FATAL program=%s (%d deneme tükendi)", self.name, retries)
                    return
                delay = self._backoff_delay(retries)
                log.info("backoff program=%s %.1fs sonra yeniden denenecek", self.name, delay)
                if self._sleep_interruptible(delay):
                    break
                continue

            # RUNNING iken çıktı.
            self._set_state(State.EXITED)
            self._emit("exit", f"exitcode={ec}")
            if not self._should_restart(ec):
                log.info("yeniden başlatma yok program=%s (politika=%s)", self.name, self.program.autorestart)
                return
            self._emit("crash", f"beklenmedik çıkış (exitcode={ec}), yeniden başlatılıyor")
            # RUNNING'den sonra restart: kısa bir nefes payı bırak.
            if self._sleep_interruptible(self.program.backoff_base):
                break

        # Döngü dışına çıkıldıysa (shutdown/stop) süreci temizle.
        self._terminate()
        self.pid = None
        if self.state not in (State.FATAL,):
            self._set_state(State.STOPPED)

    def _sleep_interruptible(self, seconds: float) -> bool:
        """seconds kadar uyur; bu sırada durdurma istenirse True döner."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self._shutdown.is_set() or self._stop_requested.is_set():
                return True
            time.sleep(0.1)
        return False

    def _join_readers(self) -> None:
        for t in self._readers:
            t.join(timeout=2)
        self._readers = []

    # ------------------------------------------------------------------ api
    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop_requested.clear()
            self._thread = threading.Thread(
                target=self._monitor, name=f"monitor-{self.name}", daemon=True
            )
            self._thread.start()

    def request_stop(self) -> None:
        self._stop_requested.set()

    def request_shutdown(self) -> None:
        self._shutdown.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout=timeout)

    def restart(self) -> None:
        """Süreci durdurup tekrar başlatır (health-check tetikleyebilir)."""
        log.info("yeniden başlatılıyor program=%s", self.name)
        self._terminate()
        # monitör döngüsü çıkışı yakalayıp RUNNING->EXITED akışıyla restart eder;
        # ancak monitör bittiyse yeniden başlat.
        if not (self._thread and self._thread.is_alive()):
            self.start()
