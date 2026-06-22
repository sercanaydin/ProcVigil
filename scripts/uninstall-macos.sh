#!/usr/bin/env bash
#
# ProcVigil kaldirma scripti (macOS / launchd).
# install-macos.sh'in olusturdugu her seyi temizler.
#
# Kullanim:
#   sudo ./scripts/uninstall-macos.sh              # uygulamayi kaldirir, config+loglari sorar
#   sudo ./scripts/uninstall-macos.sh --purge      # config ve loglari da siler (sormadan)
#   sudo ./scripts/uninstall-macos.sh --keep-data  # config ve loglari kesinlikle korur
#
set -euo pipefail

APP_DIR="/opt/procvigil"
CONFIG_DIR="/etc/procvigil"
LOG_DIR="/usr/local/var/log/procvigil"
SOCKET_PATH="/usr/local/var/run/procvigil.sock"
BIN="/usr/local/bin/pvctl"
PLIST_LABEL="com.procvigil.daemon"
PLIST_DEST="/Library/LaunchDaemons/${PLIST_LABEL}.plist"

PURGE="ask"
for arg in "$@"; do
  case "$arg" in
    --purge) PURGE="yes" ;;
    --keep-data) PURGE="no" ;;
    -h|--help)
      grep '^#' "$0" | grep -v '^#!' | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Bilinmeyen secenek: $arg (yardim: --help)" >&2; exit 2 ;;
  esac
done

if [[ "$(uname)" != "Darwin" ]]; then
  echo "Bu script macOS icindir. Linux icin: scripts/uninstall.sh" >&2
  exit 1
fi

if [[ "${EUID}" -ne 0 ]]; then
  echo "Bu script root ile calistirilmali: sudo $0" >&2
  exit 1
fi

echo ">> Servis durduruluyor (launchd bootout)..."
launchctl bootout "system/${PLIST_LABEL}" 2>/dev/null || true
launchctl bootout system "${PLIST_DEST}" 2>/dev/null || true

echo ">> LaunchDaemon, uygulama, komut ve soket temizleniyor..."
rm -f  "${PLIST_DEST}"
rm -rf "${APP_DIR}"
rm -f  "${BIN}"
rm -f  "${SOCKET_PATH}"

remove_data() {
  rm -rf "${CONFIG_DIR}" "${LOG_DIR}"
  echo ">> Config (${CONFIG_DIR}) ve loglar (${LOG_DIR}) silindi."
}

case "${PURGE}" in
  yes) remove_data ;;
  no)  echo ">> Config ve loglar korundu." ;;
  ask)
    if [[ -t 0 ]]; then
      read -r -p "Config (${CONFIG_DIR}) ve loglar (${LOG_DIR}) da silinsin mi? [e/H] " ans
      case "${ans}" in [eE]*) remove_data ;; *) echo ">> Config ve loglar korundu." ;; esac
    else
      echo ">> Config ve loglar korundu (silmek icin: --purge)."
    fi
    ;;
esac

echo "ProcVigil kaldirildi."
