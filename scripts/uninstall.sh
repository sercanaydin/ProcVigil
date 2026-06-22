#!/usr/bin/env bash
#
# ProcVigil kaldirma scripti (Ubuntu / Debian tabanli sistemler - systemd).
# install.sh'in olusturdugu her seyi temizler.
#
# Kullanim:
#   sudo ./scripts/uninstall.sh              # uygulamayi kaldirir, config+loglari sorar
#   sudo ./scripts/uninstall.sh --purge      # config ve loglari da siler (sormadan)
#   sudo ./scripts/uninstall.sh --keep-data  # config ve loglari kesinlikle korur
#
set -euo pipefail

APP_DIR="/opt/procvigil"
CONFIG_DIR="/etc/procvigil"
LOG_DIR="/var/log/procvigil"
SERVICE_FILE="/etc/systemd/system/procvigil.service"
BIN="/usr/local/bin/pvctl"
RUN_DIR="/run/procvigil"

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

if [[ "${EUID}" -ne 0 ]]; then
  echo "Bu script root ile calistirilmali: sudo $0" >&2
  exit 1
fi

echo ">> Servis durduruluyor ve devre disi birakiliyor..."
systemctl disable --now procvigil.service 2>/dev/null || true
rm -f "${SERVICE_FILE}"
systemctl daemon-reload 2>/dev/null || true

echo ">> Uygulama, komut ve runtime dizini temizleniyor..."
rm -rf "${APP_DIR}"
rm -f  "${BIN}"
rm -rf "${RUN_DIR}"

if getent group procvigil >/dev/null 2>&1; then
  echo ">> 'procvigil' grubu kaldiriliyor..."
  groupdel procvigil 2>/dev/null || echo "   (grup kaldirilamadi - hala uyesi olabilir, atlandi)"
fi

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
