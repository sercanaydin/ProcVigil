"""ProcessInstance testleri: durum makinesi, restart, FATAL, shutdown.

Bu testler gerçek alt süreçler başlatır (entegrasyon testi). Süreler kısa
tutulmuştur ama yine de zamanlamaya dayalı oldukları için cömert timeout'lar
kullanılır.
"""

from __future__ import annotations

from procvigil.config import ProgramConfig
from procvigil.process import ProcessInstance, State

from .conftest import wait_for


def make_instance(tmp_path, **overrides) -> ProcessInstance:
    fields = dict(
        name="test",
        command="sleep 30",
    )
    fields.update(overrides)
    program = ProgramConfig(**fields)
    return ProcessInstance(program, index=0, hostname="testhost", logdir=str(tmp_path))


def test_alive_process_reaches_running(tmp_path):
    inst = make_instance(tmp_path, command="sleep 30", startsecs=0.4)
    inst.start()
    try:
        assert wait_for(lambda: inst.state == State.RUNNING, timeout=5.0)
        assert inst.pid is not None
        assert inst.start_count == 1
    finally:
        inst.request_shutdown()
        inst.join(timeout=10)


def test_shutdown_stops_running_process(tmp_path):
    inst = make_instance(tmp_path, command="sleep 30", startsecs=0.4)
    inst.start()
    assert wait_for(lambda: inst.state == State.RUNNING, timeout=5.0)
    inst.request_shutdown()
    inst.join(timeout=10)
    assert inst.state == State.STOPPED
    assert inst.pid is None


def test_instant_failure_reaches_fatal(tmp_path):
    # startsecs dolmadan sürekli ölen süreç -> startretries sonra FATAL.
    inst = make_instance(
        tmp_path,
        command="sh -c 'exit 1'",
        startsecs=2.0,
        startretries=2,
        backoff_base=0.1,
        backoff_max=0.2,
    )
    inst.start()
    inst.join(timeout=10)
    assert inst.state == State.FATAL
    assert inst.start_count == 2


def test_running_process_restarts_on_crash(tmp_path):
    # startsecs'i kısa, böylece RUNNING'e geçer; sonra ölür -> always restart.
    inst = make_instance(
        tmp_path,
        command="sh -c 'sleep 0.3; exit 1'",
        startsecs=0.1,
        autorestart="always",
        backoff_base=0.1,
    )
    inst.start()
    try:
        assert wait_for(lambda: inst.start_count >= 2, timeout=8.0)
    finally:
        inst.request_shutdown()
        inst.join(timeout=10)


def test_autorestart_never_stays_exited(tmp_path):
    inst = make_instance(
        tmp_path,
        command="sh -c 'sleep 0.3; exit 0'",
        startsecs=0.1,
        autorestart="never",
    )
    inst.start()
    inst.join(timeout=10)
    assert inst.state == State.EXITED
    assert inst.start_count == 1


def test_unexpected_policy_does_not_restart_on_expected_exit(tmp_path):
    # autorestart=unexpected + exitcodes=[0]: 0 ile çıkınca restart yok.
    inst = make_instance(
        tmp_path,
        command="sh -c 'sleep 0.3; exit 0'",
        startsecs=0.1,
        autorestart="unexpected",
        exitcodes=[0],
    )
    inst.start()
    inst.join(timeout=10)
    assert inst.state == State.EXITED
    assert inst.start_count == 1


def test_program_output_is_logged(tmp_path):
    inst = make_instance(
        tmp_path,
        name="logger-test",
        command="sh -c 'echo merhaba-dunya; sleep 30'",
        startsecs=0.2,
    )
    inst.start()
    try:
        assert wait_for(lambda: inst.state == State.RUNNING, timeout=5.0)
        logfile = tmp_path / "logger-test.out.log"
        assert wait_for(lambda: logfile.exists() and "merhaba-dunya" in logfile.read_text())
    finally:
        inst.request_shutdown()
        inst.join(timeout=10)
