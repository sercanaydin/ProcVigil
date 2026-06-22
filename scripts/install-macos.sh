#!/usr/bin/env bash
#
# ProcVigil kurulum scripti (macOS / launchd).
# Linux'taki install.sh'in launchd karsiligi. venv/pip GEREKTIRMEZ — saf stdlib;
# sistem python3 ile calisir. config + log dizinlerini hazirlar, LaunchDaemon yukler.
#
# Kullanim:  sudo ./scripts/install-macos.sh
#
set -euo pipefail

APP_DIR="/opt/procvigil"
CONFIG_DIR="/etc/procvigil"
CONFIG_FILE="${CONFIG_DIR}/procvigil.json"   # varsayilan: JSON (bagimliliksiz)
LOG_DIR="/usr/local/var/log/procvigil"
# macOS'ta /run yoktur; soketi macOS uygun yoluna koy (Linux'taki /run/procvigil karsiligi).
SOCKET_DIR="/usr/local/var/run"
SOCKET_PATH="${SOCKET_DIR}/procvigil.sock"
PLIST_LABEL="com.procvigil.daemon"
PLIST_DEST="/Library/LaunchDaemons/${PLIST_LABEL}.plist"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"

if [[ "$(uname)" != "Darwin" ]]; then
  echo "Bu script macOS icindir. Linux icin: scripts/install.sh" >&2
  exit 1
fi

if [[ "${EUID}" -ne 0 ]]; then
  echo "Bu script root ile calistirilmali: sudo $0" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 bulunamadi. Once kurun:  brew install python   veya  xcode-select --install" >&2
  exit 1
fi

echo ">> Uygulama dosyalari ${APP_DIR} altina kopyalaniyor..."
mkdir -p "${APP_DIR}"
# Temiz kopya: eski surumun .py/.pyc kalintilari kalmasin (idempotent guncelleme).
rm -rf "${APP_DIR}/procvigil"
cp -r "${PROJECT_DIR}/procvigil" "${APP_DIR}/"

# pvctl kisayolu (supervisorctl muadili) — sistem python3 + PYTHONPATH ile.
cat > /usr/local/bin/pvctl <<EOF
#!/usr/bin/env bash
exec env PYTHONPATH="${APP_DIR}" python3 -m procvigil ctl -c "${CONFIG_FILE}" "\$@"
EOF
chmod +x /usr/local/bin/pvctl

echo ">> Dizinler olusturuluyor..."
mkdir -p "${CONFIG_DIR}" "${LOG_DIR}" "${SOCKET_DIR}"

if [[ ! -f "${CONFIG_FILE}" ]]; then
  echo ">> Ornek config yerlestiriliyor: ${CONFIG_FILE}"
  cp "${PROJECT_DIR}/config/procvigil.json" "${CONFIG_FILE}"
  # macOS'a uygun log ve soket yollarina cevir.
  sed -i '' "s#/var/log/procvigil#${LOG_DIR}#" "${CONFIG_FILE}" || true
  sed -i '' "s#/run/procvigil/procvigil.sock#${SOCKET_PATH}#" "${CONFIG_FILE}" || true
else
  echo ">> Mevcut config korunuyor: ${CONFIG_FILE}"
fi

echo ">> Config dogrulaniyor..."
PYTHONPATH="${APP_DIR}" python3 -m procvigil check -c "${CONFIG_FILE}" || {
  echo "UYARI: Config dogrulamasi basarisiz. Lutfen ${CONFIG_FILE} dosyasini duzenleyin." >&2
}

echo ">> LaunchDaemon yerlestiriliyor: ${PLIST_DEST}"
cp "${PROJECT_DIR}/launchd/${PLIST_LABEL}.plist" "${PLIST_DEST}"
# launchd, plist'in root:wheel sahipli ve grup/dunya tarafindan yazilamaz olmasini ister.
chown root:wheel "${PLIST_DEST}"
chmod 644 "${PLIST_DEST}"

echo ">> Servis yukleniyor (bootstrap)..."
# Onceden yuklu ise once kaldir (idempotent kurulum).
launchctl bootout system "${PLIST_DEST}" 2>/dev/null || true
launchctl bootstrap system "${PLIST_DEST}"
launchctl enable "system/${PLIST_LABEL}"

cat <<EOF

============================================================
ProcVigil kuruldu (macOS / launchd, venv/pip yok — saf stdlib).

  Config:   ${CONFIG_FILE}
  Loglar:   ${LOG_DIR}/          (program ciktilari)
  Daemon:   ${LOG_DIR}/procvigil.daemon.log

Sik kullanilan komutlar (sudo gerektirir):
  sudo launchctl kickstart -k system/${PLIST_LABEL}   # baslat / yeniden baslat
  sudo launchctl print system/${PLIST_LABEL}          # durum / detay
  sudo launchctl kill -HUP system/${PLIST_LABEL}      # config reload (SIGHUP)
  sudo launchctl bootout system/${PLIST_LABEL}        # durdur + kaldir
  tail -f ${LOG_DIR}/procvigil.daemon.log                # daemon loglarini izle

Canli program yonetimi (supervisorctl muadili):
  pvctl status               # tum program durumlari (yonetici/admin ise sudo'suz)
  pvctl restart <program>    # tek programi yeniden baslat

Not: Kontrol soketi 'admin' grubuna acilir; yonetici kullanicilar 'pvctl'yi
sudo'suz kullanabilir. (Yonetici degilsen: sudo pvctl ...)

Config'i degistirdikten sonra reload:
  sudo launchctl kill -HUP system/${PLIST_LABEL}
============================================================
EOF
