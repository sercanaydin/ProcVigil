"""Supervisor testleri: çoklu program yönetimi ve config reload."""

from __future__ import annotations

import textwrap

from procvigil.config import load_config
from procvigil.process import State
from procvigil.supervisor import Supervisor

from .conftest import wait_for


def _write_config(tmp_path, body: str):
    path = tmp_path / "procvigil.yml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return str(path)


def _make_supervisor(tmp_path, programs_yaml: str) -> Supervisor:
    logdir = tmp_path / "logs"
    body = f"""
    procvigil:
      logdir: {logdir}
    programs:
    {textwrap.indent(textwrap.dedent(programs_yaml), "      ")}
    """
    path = _write_config(tmp_path, body)
    cfg = load_config(path)
    return Supervisor(path, cfg)


def _running_names(sup: Supervisor) -> set[str]:
    return {row["name"] for row in sup.status() if row["state"] == State.RUNNING.value}


def test_starts_all_programs(tmp_path):
    sup = _make_supervisor(
        tmp_path,
        """
        - name: a
          command: sleep 30
          startsecs: 0.3
        - name: b
          command: sleep 30
          startsecs: 0.3
        """,
    )
    sup.start()
    try:
        assert wait_for(lambda: _running_names(sup) == {"a", "b"}, timeout=6.0)
    finally:
        sup._do_shutdown()


def test_numprocs_creates_multiple_instances(tmp_path):
    sup = _make_supervisor(
        tmp_path,
        """
        - name: worker
          command: sleep 30
          numprocs: 3
          startsecs: 0.3
        """,
    )
    sup.start()
    try:
        assert wait_for(lambda: len(sup.status()) == 3, timeout=6.0)
        names = {row["name"] for row in sup.status()}
        assert names == {"worker:0", "worker:1", "worker:2"}
    finally:
        sup._do_shutdown()


def test_reload_adds_and_removes_programs(tmp_path):
    sup = _make_supervisor(
        tmp_path,
        """
        - name: keep
          command: sleep 30
          startsecs: 0.3
        - name: remove-me
          command: sleep 30
          startsecs: 0.3
        """,
    )
    sup.start()
    try:
        assert wait_for(lambda: _running_names(sup) == {"keep", "remove-me"}, timeout=6.0)

        # Config'i değiştir: remove-me kalksın, add-me eklensin, keep dursun.
        logdir = tmp_path / "logs"
        new_body = f"""
        procvigil:
          logdir: {logdir}
        programs:
          - name: keep
            command: sleep 30
            startsecs: 0.3
          - name: add-me
            command: sleep 30
            startsecs: 0.3
        """
        (tmp_path / "procvigil.yml").write_text(textwrap.dedent(new_body), encoding="utf-8")

        sup.reload()
        assert wait_for(lambda: _running_names(sup) == {"keep", "add-me"}, timeout=6.0)
        all_names = {row["name"] for row in sup.status()}
        assert "remove-me" not in all_names
    finally:
        sup._do_shutdown()


def test_reload_keeps_unchanged_program_running(tmp_path):
    sup = _make_supervisor(
        tmp_path,
        """
        - name: stable
          command: sleep 30
          startsecs: 0.3
        """,
    )
    sup.start()
    try:
        assert wait_for(lambda: _running_names(sup) == {"stable"}, timeout=6.0)
        pid_before = sup.status()[0]["pid"]

        # Aynı config'i tekrar yaz ve reload et: stable yeniden başlamamalı.
        sup.reload()
        # Kısa bir süre bekle, durum değişmesin.
        assert wait_for(lambda: False, timeout=1.0) is False
        row = sup.status()[0]
        assert row["state"] == State.RUNNING.value
        assert row["pid"] == pid_before
    finally:
        sup._do_shutdown()
