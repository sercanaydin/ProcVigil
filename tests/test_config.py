"""Config yükleme ve doğrulama testleri."""

from __future__ import annotations

import json
import textwrap

import pytest

from procvigil.config import ConfigError, load_config


def _write(tmp_path, content: str):
    path = tmp_path / "procvigil.yml"
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return str(path)


def test_json_config_loads_without_yaml(tmp_path):
    """JSON config PyYAML olmadan (yerleşik) yüklenebilmeli."""
    data = {
        "procvigil": {"logdir": "/tmp/j", "loglevel": "warning"},
        "programs": [
            {
                "name": "jsonworker",
                "command": "php artisan queue:work --tries=3",
                "autorestart": "unexpected",
                "exitcodes": [0, 2],
                "notify": {"url": "https://example.com/hook", "events": ["crash"]},
            }
        ],
    }
    path = tmp_path / "procvigil.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    cfg = load_config(str(path))
    assert cfg.logdir == "/tmp/j"
    assert cfg.loglevel == "warning"
    p = cfg.programs[0]
    assert p.name == "jsonworker"
    assert p.argv() == ["php", "artisan", "queue:work", "--tries=3"]
    assert p.autorestart == "unexpected"
    assert p.exitcodes == [0, 2]
    assert p.notify.events == ["crash"]


def test_invalid_json_raises(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{ not valid json ", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(str(path))


def test_minimal_program_uses_defaults(tmp_path):
    cfg_path = _write(
        tmp_path,
        """
        programs:
          - name: worker
            command: /bin/true
        """,
    )
    cfg = load_config(cfg_path)
    assert len(cfg.programs) == 1
    p = cfg.programs[0]
    assert p.name == "worker"
    assert p.command == "/bin/true"
    assert p.autostart is True
    assert p.autorestart == "always"
    assert p.numprocs == 1
    assert p.exitcodes == [0]
    assert p.healthcheck is None
    assert p.notify is None


def test_argv_splits_command(tmp_path):
    cfg_path = _write(
        tmp_path,
        """
        programs:
          - name: w
            command: php artisan queue:work --tries=3
        """,
    )
    cfg = load_config(cfg_path)
    assert cfg.programs[0].argv() == ["php", "artisan", "queue:work", "--tries=3"]


def test_daemon_section_parsed(tmp_path):
    cfg_path = _write(
        tmp_path,
        """
        procvigil:
          logdir: /tmp/x
          loglevel: debug
          hostname: test-host
        programs:
          - name: w
            command: /bin/true
        """,
    )
    cfg = load_config(cfg_path)
    assert cfg.logdir == "/tmp/x"
    assert cfg.loglevel == "debug"
    assert cfg.hostname == "test-host"


def test_autorestart_bool_is_normalized(tmp_path):
    cfg_path = _write(
        tmp_path,
        """
        programs:
          - name: a
            command: /bin/true
            autorestart: false
          - name: b
            command: /bin/true
            autorestart: true
        """,
    )
    cfg = load_config(cfg_path)
    assert cfg.programs[0].autorestart == "never"
    assert cfg.programs[1].autorestart == "always"


def test_stopsignal_strips_sig_prefix(tmp_path):
    cfg_path = _write(
        tmp_path,
        """
        programs:
          - name: a
            command: /bin/true
            stopsignal: SIGINT
        """,
    )
    cfg = load_config(cfg_path)
    assert cfg.programs[0].stopsignal == "INT"


def test_healthcheck_and_notify_parsed(tmp_path):
    cfg_path = _write(
        tmp_path,
        """
        programs:
          - name: api
            command: /bin/true
            healthcheck:
              url: http://localhost:8080/health
              interval: 5
              expect_status: 204
              on_unhealthy: notify
            notify:
              url: https://example.com/hook
              events: [crash, fatal]
              headers:
                Authorization: Bearer x
        """,
    )
    cfg = load_config(cfg_path)
    p = cfg.programs[0]
    assert p.healthcheck.url == "http://localhost:8080/health"
    assert p.healthcheck.interval == 5
    assert p.healthcheck.expect_status == [204]
    assert p.healthcheck.on_unhealthy == "notify"
    assert p.notify.events == ["crash", "fatal"]
    assert p.notify.headers["Authorization"] == "Bearer x"


def test_missing_name_raises(tmp_path):
    cfg_path = _write(
        tmp_path,
        """
        programs:
          - command: /bin/true
        """,
    )
    with pytest.raises(ConfigError):
        load_config(cfg_path)


def test_missing_command_raises(tmp_path):
    cfg_path = _write(
        tmp_path,
        """
        programs:
          - name: w
        """,
    )
    with pytest.raises(ConfigError):
        load_config(cfg_path)


def test_duplicate_names_raise(tmp_path):
    cfg_path = _write(
        tmp_path,
        """
        programs:
          - name: dup
            command: /bin/true
          - name: dup
            command: /bin/false
        """,
    )
    with pytest.raises(ConfigError, match="dup"):
        load_config(cfg_path)


def test_invalid_autorestart_raises(tmp_path):
    cfg_path = _write(
        tmp_path,
        """
        programs:
          - name: w
            command: /bin/true
            autorestart: sometimes
        """,
    )
    with pytest.raises(ConfigError):
        load_config(cfg_path)


def test_invalid_notify_event_raises(tmp_path):
    cfg_path = _write(
        tmp_path,
        """
        programs:
          - name: w
            command: /bin/true
            notify:
              url: http://x
              events: [boom]
        """,
    )
    with pytest.raises(ConfigError, match="boom"):
        load_config(cfg_path)


def test_healthcheck_without_url_raises(tmp_path):
    cfg_path = _write(
        tmp_path,
        """
        programs:
          - name: w
            command: /bin/true
            healthcheck:
              interval: 5
        """,
    )
    with pytest.raises(ConfigError):
        load_config(cfg_path)


def test_missing_file_raises():
    with pytest.raises(ConfigError):
        load_config("/nonexistent/path/procvigil.yml")
