"""Config yükleme ve doğrulama.

Config dosyası JSON veya YAML formatında olabilir. İki üst seviye bölüm vardır:

    procvigil: -> daemon geneli ayarlar (log dizini, log seviyesi, hostname)
    programs:  -> gözetilecek programların listesi

JSON desteği yerleşiktir (ek bağımlılık gerekmez). YAML kullanmak için PyYAML
gerekir (``pip install pyyaml`` veya ``apt install python3-yaml``); kurulu
değilse ``.yml``/``.yaml`` dosyaları için anlaşılır bir hata verilir.

Her program en az bir ``name`` ve ``command`` içermelidir. Geri kalan tüm
alanların makul varsayılanları vardır.
"""

from __future__ import annotations

import json
import os
import shlex
import socket
from dataclasses import dataclass, field
from typing import Any


class ConfigError(Exception):
    """Config dosyası okunamadığında / geçersiz olduğunda fırlatılır."""


# autorestart için geçerli değerler (supervisor ile aynı semantik):
#   always     -> her çıkışta yeniden başlat
#   unexpected -> sadece beklenmeyen exit kodlarında yeniden başlat
#   never      -> asla yeniden başlatma
RESTART_POLICIES = {"always", "unexpected", "never"}

# Olay tipleri: hangi durumda webhook tetiklenir.
VALID_EVENTS = {"start", "exit", "crash", "restart", "fatal", "unhealthy"}


@dataclass
class HealthCheck:
    """HTTP tabanlı canlılık denetimi (opsiyonel)."""

    url: str
    interval: float = 30.0
    timeout: float = 5.0
    method: str = "GET"
    expect_status: list[int] = field(default_factory=lambda: [200])
    unhealthy_threshold: int = 3
    # on_unhealthy: "restart" | "notify" | "nothing"
    on_unhealthy: str = "restart"


@dataclass
class Notify:
    """Olay anında atılacak HTTP isteği (webhook)."""

    url: str
    method: str = "POST"
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = 5.0
    # Hangi olaylarda tetiklenecek.
    events: list[str] = field(default_factory=lambda: ["start", "crash", "restart", "fatal"])
    # İstek başarısız olursa kaç kez tekrar denensin.
    retries: int = 2
    retry_backoff: float = 1.0


@dataclass
class ProgramConfig:
    """Tek bir gözetilen programın tüm ayarları."""

    name: str
    command: str
    directory: str | None = None
    user: str | None = None
    environment: dict[str, str] = field(default_factory=dict)

    autostart: bool = True
    # always | unexpected | never
    autorestart: str = "always"
    # Beklenen (normal) çıkış kodları; "unexpected" politikası bunlara bakar.
    exitcodes: list[int] = field(default_factory=lambda: [0])

    # Süreç RUNNING sayılmadan önce kesintisiz ayakta kalması gereken saniye.
    startsecs: float = 3.0
    # FATAL'a düşmeden önce ardışık başlatma deneme sayısı.
    startretries: int = 5
    # Backoff (yeniden deneme bekleme) ayarları.
    backoff_base: float = 1.0
    backoff_max: float = 30.0

    # Durdururken gönderilecek sinyal ve sonrasında SIGKILL'e kadar bekleme.
    stopsignal: str = "TERM"
    stopwaitsecs: float = 10.0

    # Aynı program için kaç paralel instance çalıştırılacak.
    numprocs: int = 1

    # Log dosyaları (None ise daemon log dizinine otomatik yazılır).
    stdout_logfile: str | None = None
    stderr_logfile: str | None = None
    # Tek log dosyasının döndürülmeden önceki maksimum boyutu (byte).
    logfile_maxbytes: int = 10 * 1024 * 1024
    logfile_backups: int = 5

    healthcheck: HealthCheck | None = None
    notify: Notify | None = None

    def argv(self) -> list[str]:
        """command string'ini exec için argv listesine çevirir."""
        return shlex.split(self.command)


@dataclass
class MetricsConfig:
    """Opsiyonel observability HTTP sunucusu (/status + /metrics)."""

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 9100


@dataclass
class DaemonConfig:
    """Daemon geneli ayarlar + program listesi."""

    logdir: str = "/var/log/procvigil"
    loglevel: str = "info"
    hostname: str = field(default_factory=socket.gethostname)
    # pvctl'nin bağlanacağı Unix socket (supervisorctl muadili).
    socket_path: str = "/run/procvigil/procvigil.sock"
    # Yeni süreçler için umask (None ise değiştirilmez). Örn: "022".
    umask: str | None = None
    metrics: MetricsConfig = field(default_factory=MetricsConfig)
    programs: list[ProgramConfig] = field(default_factory=list)


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _parse_healthcheck(raw: dict[str, Any], prog_name: str) -> HealthCheck:
    if "url" not in raw:
        raise ConfigError(f"'{prog_name}' programının healthcheck bloğunda 'url' zorunlu.")
    on_unhealthy = str(raw.get("on_unhealthy", "restart"))
    if on_unhealthy not in {"restart", "notify", "nothing"}:
        raise ConfigError(
            f"'{prog_name}' healthcheck.on_unhealthy 'restart|notify|nothing' olmalı, "
            f"verilen: {on_unhealthy!r}"
        )
    return HealthCheck(
        url=str(raw["url"]),
        interval=float(raw.get("interval", 30.0)),
        timeout=float(raw.get("timeout", 5.0)),
        method=str(raw.get("method", "GET")).upper(),
        expect_status=[int(c) for c in _as_list(raw.get("expect_status", [200]))],
        unhealthy_threshold=int(raw.get("unhealthy_threshold", 3)),
        on_unhealthy=on_unhealthy,
    )


def _parse_notify(raw: dict[str, Any], prog_name: str) -> Notify:
    if "url" not in raw:
        raise ConfigError(f"'{prog_name}' programının notify bloğunda 'url' zorunlu.")
    events = [str(e).lower() for e in _as_list(raw.get("events", ["start", "crash", "restart", "fatal"]))]
    invalid = set(events) - VALID_EVENTS
    if invalid:
        raise ConfigError(
            f"'{prog_name}' notify.events içinde geçersiz olay(lar): {sorted(invalid)}. "
            f"Geçerli: {sorted(VALID_EVENTS)}"
        )
    headers = raw.get("headers", {}) or {}
    if not isinstance(headers, dict):
        raise ConfigError(f"'{prog_name}' notify.headers bir sözlük (key: value) olmalı.")
    return Notify(
        url=str(raw["url"]),
        method=str(raw.get("method", "POST")).upper(),
        headers={str(k): str(v) for k, v in headers.items()},
        timeout=float(raw.get("timeout", 5.0)),
        events=events,
        retries=int(raw.get("retries", 2)),
        retry_backoff=float(raw.get("retry_backoff", 1.0)),
    )


def _parse_program(raw: dict[str, Any]) -> ProgramConfig:
    if not isinstance(raw, dict):
        raise ConfigError(f"Her program bir sözlük olmalı, verilen: {type(raw).__name__}")
    name = raw.get("name")
    if not name:
        raise ConfigError("Her programın bir 'name' alanı olmalı.")
    command = raw.get("command")
    if not command:
        raise ConfigError(f"'{name}' programının bir 'command' alanı olmalı.")

    autorestart = raw.get("autorestart", "always")
    # YAML'da true/false yazılabilsin diye boolean'ı da kabul et.
    if isinstance(autorestart, bool):
        autorestart = "always" if autorestart else "never"
    autorestart = str(autorestart)
    if autorestart not in RESTART_POLICIES:
        raise ConfigError(
            f"'{name}' autorestart 'always|unexpected|never' olmalı, verilen: {autorestart!r}"
        )

    env = raw.get("environment", {}) or {}
    if not isinstance(env, dict):
        raise ConfigError(f"'{name}' environment bir sözlük olmalı.")

    numprocs = int(raw.get("numprocs", 1))
    if numprocs < 1:
        raise ConfigError(f"'{name}' numprocs en az 1 olmalı.")

    healthcheck = None
    if raw.get("healthcheck"):
        healthcheck = _parse_healthcheck(raw["healthcheck"], name)

    notify = None
    if raw.get("notify"):
        notify = _parse_notify(raw["notify"], name)

    return ProgramConfig(
        name=str(name),
        command=str(command),
        directory=raw.get("directory"),
        user=raw.get("user"),
        environment={str(k): str(v) for k, v in env.items()},
        autostart=bool(raw.get("autostart", True)),
        autorestart=autorestart,
        exitcodes=[int(c) for c in _as_list(raw.get("exitcodes", [0]))],
        startsecs=float(raw.get("startsecs", 3.0)),
        startretries=int(raw.get("startretries", 5)),
        backoff_base=float(raw.get("backoff_base", 1.0)),
        backoff_max=float(raw.get("backoff_max", 30.0)),
        stopsignal=str(raw.get("stopsignal", "TERM")).upper().removeprefix("SIG"),
        stopwaitsecs=float(raw.get("stopwaitsecs", 10.0)),
        numprocs=numprocs,
        stdout_logfile=raw.get("stdout_logfile"),
        stderr_logfile=raw.get("stderr_logfile"),
        logfile_maxbytes=int(raw.get("logfile_maxbytes", 10 * 1024 * 1024)),
        logfile_backups=int(raw.get("logfile_backups", 5)),
        healthcheck=healthcheck,
        notify=notify,
    )


def _parse_raw(text: str, path: str) -> dict:
    """Config metnini JSON (yerleşik) veya YAML (opsiyonel) olarak ayrıştırır."""
    is_json = path.endswith(".json")
    is_yaml = path.endswith((".yml", ".yaml"))

    # Uzantı JSON ise ya da YAML/diğer ama PyYAML yoksa: JSON dene.
    if is_json:
        try:
            return json.loads(text) or {}
        except json.JSONDecodeError as exc:
            raise ConfigError(f"Config JSON olarak ayrıştırılamadı: {exc}") from exc

    try:
        import yaml  # noqa: PLC0415 - opsiyonel bağımlılık, gerektiğinde yüklenir
    except ImportError as exc:
        # YAML yok: JSON'a düş (JSON, YAML'ın bir alt kümesidir).
        try:
            return json.loads(text) or {}
        except json.JSONDecodeError:
            hint = (
                "YAML config için PyYAML gerekli. Kurun (apt install python3-yaml) "
                "ya da config'i JSON formatında yazın (.json)."
            )
            raise ConfigError(f"{hint} (dosya: {path})") from exc

    try:
        return yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        fmt = "YAML" if is_yaml else "YAML/JSON"
        raise ConfigError(f"Config {fmt} olarak ayrıştırılamadı: {exc}") from exc


def load_config(path: str) -> DaemonConfig:
    """Config dosyasını (JSON veya YAML) okuyup doğrulanmış DaemonConfig döner."""
    if not os.path.isfile(path):
        raise ConfigError(f"Config dosyası bulunamadı: {path}")

    with open(path, encoding="utf-8") as fh:
        raw = _parse_raw(fh.read(), path)

    if not isinstance(raw, dict):
        raise ConfigError("Config dosyasının kökü bir sözlük (object) olmalı.")

    daemon_raw = raw.get("procvigil", {}) or {}
    programs_raw = raw.get("programs", []) or []
    if not isinstance(programs_raw, list):
        raise ConfigError("'programs' bir liste olmalı.")

    programs = [_parse_program(p) for p in programs_raw]

    names = [p.name for p in programs]
    duplicates = {n for n in names if names.count(n) > 1}
    if duplicates:
        raise ConfigError(f"Aynı isimli birden fazla program var: {sorted(duplicates)}")

    metrics_raw = daemon_raw.get("metrics", {}) or {}
    if not isinstance(metrics_raw, dict):
        raise ConfigError("'procvigil.metrics' bir sözlük olmalı.")
    metrics = MetricsConfig(
        enabled=bool(metrics_raw.get("enabled", False)),
        host=str(metrics_raw.get("host", "127.0.0.1")),
        port=int(metrics_raw.get("port", 9100)),
    )

    umask = daemon_raw.get("umask")
    if umask is not None:
        umask = str(umask)

    cfg = DaemonConfig(
        logdir=str(daemon_raw.get("logdir", "/var/log/procvigil")),
        loglevel=str(daemon_raw.get("loglevel", "info")).lower(),
        socket_path=str(daemon_raw.get("socket", "/run/procvigil/procvigil.sock")),
        umask=umask,
        metrics=metrics,
        programs=programs,
    )
    if daemon_raw.get("hostname"):
        cfg.hostname = str(daemon_raw["hostname"])
    return cfg
