"""ProcVigil komut satırı giriş noktası.

Kullanım:
    python3 -m procvigil run    -c /etc/procvigil/procvigil.json   # daemon'u çalıştır
    python3 -m procvigil check  -c /etc/procvigil/procvigil.json   # config doğrula
    python3 -m procvigil ctl status                          # canlı durum (pvctl)
    python3 -m procvigil ctl restart <program>               # program yönetimi
"""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .config import ConfigError, load_config
from .control import ControlError, send_command
from .logger import setup_daemon_logger
from .supervisor import Supervisor

DEFAULT_CONFIG = "/etc/procvigil/procvigil.json"
DEFAULT_SOCKET = "/run/procvigil/procvigil.sock"


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"Config hatası: {exc}", file=sys.stderr)
        return 2

    log = setup_daemon_logger(cfg.loglevel)
    log.info("ProcVigil v%s (config=%s)", __version__, args.config)
    supervisor = Supervisor(args.config, cfg)
    try:
        supervisor.run_forever()
    except KeyboardInterrupt:
        pass
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"GEÇERSİZ: {exc}", file=sys.stderr)
        return 2
    print(f"OK: {len(cfg.programs)} program tanımı doğrulandı.")
    for p in cfg.programs:
        extras = []
        if p.numprocs > 1:
            extras.append(f"numprocs={p.numprocs}")
        if p.healthcheck:
            extras.append("healthcheck")
        if p.notify:
            extras.append("notify")
        suffix = f" [{', '.join(extras)}]" if extras else ""
        print(f"  - {p.name}: autorestart={p.autorestart}{suffix}")
    return 0


def _resolve_socket(args: argparse.Namespace) -> str:
    if getattr(args, "socket", None):
        return args.socket
    # -c verildiyse config'ten soket yolunu türet.
    cfg_path = getattr(args, "config", None)
    if cfg_path:
        try:
            return load_config(cfg_path).socket_path
        except ConfigError:
            pass
    return DEFAULT_SOCKET


def _print_status_table(rows: list[dict]) -> None:
    if not rows:
        print("(çalışan program yok)")
        return
    name_w = max(len(r["name"]) for r in rows + [{"name": "PROGRAM"}])
    print(f"{'PROGRAM'.ljust(name_w)}  {'DURUM':<9} {'PID':>7}  BAŞLATMA  EXIT")
    for r in rows:
        pid = r["pid"] if r["pid"] is not None else "-"
        ec = r["exitcode"] if r["exitcode"] is not None else "-"
        print(
            f"{r['name'].ljust(name_w)}  {r['state']:<9} {str(pid):>7}  "
            f"{r['starts']:>8}  {ec}"
        )


def _cmd_ctl(args: argparse.Namespace) -> int:
    socket_path = _resolve_socket(args)
    request: dict = {"cmd": args.action}
    if args.action in ("start", "stop", "restart", "tail"):
        if not args.name:
            print(f"'{args.action}' için program adı gerekli.", file=sys.stderr)
            return 2
        request["name"] = args.name
    if args.action == "tail":
        request["stream"] = "err" if args.stderr else "out"
        request["lines"] = args.lines

    try:
        resp = send_command(socket_path, request)
    except ControlError as exc:
        print(f"Hata: {exc}", file=sys.stderr)
        return 1

    if args.action == "status" and resp.get("ok"):
        _print_status_table(resp.get("data", []))
        return 0

    if resp.get("message"):
        print(resp["message"])
    return 0 if resp.get("ok") else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="procvigil",
        description="Supervisor mantığında hafif süreç gözcüsü.",
    )
    parser.add_argument("--version", action="version", version=f"ProcVigil {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Daemon'u ön planda çalıştır (systemd için).")
    run_p.add_argument("-c", "--config", default=DEFAULT_CONFIG, help="Config dosyası yolu.")
    run_p.set_defaults(func=_cmd_run)

    check_p = sub.add_parser("check", help="Config dosyasını doğrula ve çık.")
    check_p.add_argument("-c", "--config", default=DEFAULT_CONFIG, help="Config dosyası yolu.")
    check_p.set_defaults(func=_cmd_check)

    ctl_p = sub.add_parser(
        "ctl",
        help="Çalışan daemon'u yönet (status/start/stop/restart/reload/tail).",
    )
    ctl_p.add_argument("-s", "--socket", help="Kontrol soketi yolu (varsayılan: config'ten).")
    ctl_p.add_argument("-c", "--config", default=DEFAULT_CONFIG, help="Soket yolunu türetmek için config.")
    ctl_sub = ctl_p.add_subparsers(dest="action", required=True)

    ctl_sub.add_parser("status", help="Tüm program/instance durumları.")
    ctl_sub.add_parser("ping", help="Daemon canlı mı?")
    ctl_sub.add_parser("reload", help="Config'i yeniden yükle.")
    for action in ("start", "stop", "restart"):
        sp = ctl_sub.add_parser(action, help=f"Bir programı {action} et.")
        sp.add_argument("name", help="Program adı.")
    tail_sp = ctl_sub.add_parser("tail", help="Bir programın son log satırları.")
    tail_sp.add_argument("name", help="Program adı.")
    tail_sp.add_argument("-n", "--lines", type=int, default=50, help="Satır sayısı.")
    tail_sp.add_argument("-e", "--stderr", action="store_true", help="stderr logunu göster.")

    ctl_p.set_defaults(func=_cmd_ctl)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
