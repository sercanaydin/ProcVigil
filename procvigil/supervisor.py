"""Supervisor: tüm programları ve instance'ları yönetir.

Sorumlulukları:
- Config'teki her program için ``numprocs`` kadar ProcessInstance oluşturmak.
- autostart olanları başlatmak ve hepsini ayakta tutmak (gözcüleri ProcessInstance yapar).
- Her program için opsiyonel HealthMonitor'ü kurmak.
- Program bazında start/stop/restart (pvctl bunları kullanır).
- Sinyalleri yönetmek: SIGTERM/SIGINT -> düzgün kapanış, SIGHUP -> config reload.
- Opsiyonel kontrol soketi (pvctl) ve metrics HTTP sunucusunu yaşatmak.
"""

from __future__ import annotations

import logging
import os
import signal
import threading

from .config import DaemonConfig, ProgramConfig, load_config
from .notifier import HealthMonitor
from .process import ProcessInstance, State

log = logging.getLogger("procvigil")


class Supervisor:
    def __init__(self, config_path: str, config: DaemonConfig) -> None:
        self.config_path = config_path
        self.config = config
        # program adı -> instance listesi
        self._instances: dict[str, list[ProcessInstance]] = {}
        # program adı -> güncel config (start/restart için gerekir)
        self._programs: dict[str, ProgramConfig] = {}
        self._health: dict[str, HealthMonitor] = {}
        self._shutdown = threading.Event()
        self._lock = threading.RLock()
        # Opsiyonel yan servisler (run_forever içinde kurulur).
        self._control = None
        self._metrics = None

    # ------------------------------------------------------------------ setup
    def _make_instances(self, program: ProgramConfig) -> list[ProcessInstance]:
        return [
            ProcessInstance(program, i, self.config.hostname, self.config.logdir)
            for i in range(program.numprocs)
        ]

    def _start_program(self, program: ProgramConfig) -> None:
        """Programı kurar (instance + health) ve autostart ise başlatır."""
        instances = self._make_instances(program)
        self._instances[program.name] = instances
        self._programs[program.name] = program
        for inst in instances:
            if program.autostart:
                inst.start()

        if program.healthcheck:
            monitor = HealthMonitor(
                program_name=program.name,
                check=program.healthcheck,
                is_running=lambda insts=instances: any(i.is_running() for i in insts),
                on_unhealthy_cb=lambda insts=instances, p=program: self._handle_unhealthy(p, insts),
            )
            self._health[program.name] = monitor
            monitor.start()

    def _handle_unhealthy(self, program: ProgramConfig, instances: list[ProcessInstance]) -> None:
        action = program.healthcheck.on_unhealthy if program.healthcheck else "nothing"
        if action == "restart":
            log.warning("health -> restart program=%s", program.name)
            for inst in instances:
                inst._emit("unhealthy", "health-check eşiği aşıldı")  # noqa: SLF001
                inst.restart()
        elif action == "notify":
            for inst in instances:
                inst._emit("unhealthy", "health-check eşiği aşıldı")  # noqa: SLF001
        # "nothing" -> sadece logla (HealthMonitor zaten loglar)

    # ------------------------------------------------------------------ run
    def start(self) -> None:
        os.makedirs(self.config.logdir, exist_ok=True)
        if self.config.umask is not None:
            try:
                os.umask(int(self.config.umask, 8))
            except ValueError:
                log.warning("geçersiz umask: %s (yok sayıldı)", self.config.umask)
        log.info(
            "ProcVigil başlıyor host=%s programs=%d logdir=%s",
            self.config.hostname,
            len(self.config.programs),
            self.config.logdir,
        )
        with self._lock:
            for program in self.config.programs:
                self._start_program(program)

    def run_forever(self) -> None:
        self._install_signal_handlers()
        self.start()
        self._start_side_services()
        # Ana thread sadece sinyalleri bekler; iş monitör thread'lerinde döner.
        while not self._shutdown.is_set():
            self._shutdown.wait(1.0)
        self._do_shutdown()

    def _start_side_services(self) -> None:
        # Geç import: bu modüller supervisor'a bağımlı olduğundan döngüyü önler.
        from .control import ControlServer

        try:
            self._control = ControlServer(self, self.config.socket_path)
            self._control.start()
        except Exception as exc:  # noqa: BLE001
            log.error("kontrol soketi başlatılamadı (%s): %s", self.config.socket_path, exc)
            self._control = None

        if self.config.metrics.enabled:
            from .metrics import MetricsServer

            try:
                self._metrics = MetricsServer(self, self.config.metrics)
                self._metrics.start()
            except Exception as exc:  # noqa: BLE001
                log.error("metrics sunucusu başlatılamadı: %s", exc)
                self._metrics = None

    # ------------------------------------------------------------------ signals
    def _install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, self._on_term)
        signal.signal(signal.SIGINT, self._on_term)
        signal.signal(signal.SIGHUP, self._on_hup)

    def _on_term(self, signum, frame) -> None:  # noqa: ANN001, ARG002
        log.info("sinyal alındı (%s) -> kapanış", signal.Signals(signum).name)
        self._shutdown.set()

    def _on_hup(self, signum, frame) -> None:  # noqa: ANN001, ARG002
        log.info("SIGHUP alındı -> config reload")
        # Sinyal handler içinde ağır iş yapma; ayrı thread'e devret.
        threading.Thread(target=self.reload, name="reload", daemon=True).start()

    # ------------------------------------------------------------------ reload
    def reload(self) -> str:
        """Config'i yeniden okur ve farkları uygular (eklenen/silinen/değişen)."""
        try:
            new_cfg = load_config(self.config_path)
        except Exception as exc:  # noqa: BLE001
            log.error("reload başarısız, eski config korunuyor: %s", exc)
            return f"reload başarısız: {exc}"

        changes: list[str] = []
        with self._lock:
            old = {p.name: p for p in self.config.programs}
            new = {p.name: p for p in new_cfg.programs}

            for name in set(old) - set(new):
                log.info("reload: program kaldırıldı -> %s", name)
                self._teardown_program(name)
                changes.append(f"-{name}")

            for name in set(new) - set(old):
                log.info("reload: yeni program -> %s", name)
                self._start_program(new[name])
                changes.append(f"+{name}")

            for name in set(new) & set(old):
                if new[name] != old[name]:
                    log.info("reload: program değişti -> %s (yeniden kuruluyor)", name)
                    self._teardown_program(name)
                    self._start_program(new[name])
                    changes.append(f"~{name}")

            self.config = new_cfg
        log.info("reload tamamlandı")
        return "reload tamamlandı" + (f" ({', '.join(changes)})" if changes else " (değişiklik yok)")

    def _teardown_program(self, name: str) -> None:
        """Programı tamamen kaldırır (reload'da silinen/değişen için)."""
        monitor = self._health.pop(name, None)
        if monitor:
            monitor.stop()
        for inst in self._instances.get(name, []):
            inst.request_shutdown()
        for inst in self._instances.get(name, []):
            inst.join(timeout=inst.program.stopwaitsecs + 5)
        self._instances.pop(name, None)
        self._programs.pop(name, None)

    # ------------------------------------------------------------------ program kontrolü (pvctl)
    def start_program(self, name: str) -> tuple[bool, str]:
        with self._lock:
            instances = self._instances.get(name)
            if instances is None:
                return False, f"bilinmeyen program: {name}"
            started = 0
            for inst in instances:
                if not inst.is_running():
                    inst.start()
                    started += 1
            if started == 0:
                return True, f"{name}: zaten çalışıyor"
            return True, f"{name}: başlatıldı ({started} instance)"

    def stop_program(self, name: str) -> tuple[bool, str]:
        with self._lock:
            instances = self._instances.get(name)
            if instances is None:
                return False, f"bilinmeyen program: {name}"
            for inst in instances:
                inst.request_stop()
        # join'i lock dışında yap ki uzun durdurma diğer komutları bloklamasın.
        for inst in instances:
            inst.join(timeout=inst.program.stopwaitsecs + 5)
        return True, f"{name}: durduruldu"

    def restart_program(self, name: str) -> tuple[bool, str]:
        ok, msg = self.stop_program(name)
        if not ok:
            return ok, msg
        return self.start_program(name)

    def tail(self, name: str, stream: str = "out", lines: int = 50) -> tuple[bool, str]:
        with self._lock:
            instances = self._instances.get(name)
            if not instances:
                return False, f"bilinmeyen program: {name}"
            path = instances[0].logfile_path(stream)
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                content = fh.readlines()
        except FileNotFoundError:
            return True, f"(log dosyası henüz yok: {path})"
        return True, "".join(content[-lines:])

    # ------------------------------------------------------------------ shutdown
    def _do_shutdown(self) -> None:
        log.info("kapanış: tüm programlar durduruluyor")
        if self._control:
            self._control.stop()
        if self._metrics:
            self._metrics.stop()
        with self._lock:
            for monitor in self._health.values():
                monitor.stop()
            for instances in self._instances.values():
                for inst in instances:
                    inst.request_shutdown()
            for instances in self._instances.values():
                for inst in instances:
                    inst.join(timeout=inst.program.stopwaitsecs + 5)
        log.info("ProcVigil durdu")

    # ------------------------------------------------------------------ status
    def status(self) -> list[dict]:
        rows: list[dict] = []
        with self._lock:
            for instances in self._instances.values():
                for inst in instances:
                    rows.append(
                        {
                            "name": inst.name,
                            "program": inst.program.name,
                            "state": inst.state.value,
                            "pid": inst.pid,
                            "starts": inst.start_count,
                            "exitcode": inst.exitcode,
                        }
                    )
        return rows

    @staticmethod
    def all_states() -> list[str]:
        return [s.value for s in State]
