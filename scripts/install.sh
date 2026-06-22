#!/usr/bin/env bash
#
# ProcVigil kurulum scripti (Ubuntu / Debian tabanlı sistemler).
# venv/pip GEREKTİRMEZ — saf stdlib; sistem python3 ile çalışır.
# Uygulamayı kopyalar, config + systemd unit'ini yerleştirir.
#
# Kullanım:  sudo ./scripts/install.sh
#
set -euo pipefail

APP_DIR="/opt/procvigil"
CONFIG_DIR="/etc/procvigil"
CONFIG_FILE="${CONFIG_DIR}/procvigil.json"   # varsayılan: JSON (bağımlılıksız)
LOG_DIR="/var/log/procvigil"
SERVICE_FILE="/etc/systemd/system/procvigil.service"

# Bu scriptin bulunduğu proje kökü.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "${SCRIPT_DIR}")"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Bu script root ile çalıştırılmalı: sudo $0" >&2
  exit 1
fi

echo ">> python3 kontrol ediliyor (tek gereksinim)..."
if ! command -v python3 >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y python3
fi

echo ">> Uygulama dosyaları ${APP_DIR} altına kopyalanıyor..."
mkdir -p "${APP_DIR}"
cp -r "${PROJECT_DIR}/procvigil" "${APP_DIR}/"

# pvctl kısayolu (supervisorctl muadili) — sistem python3 + PYTHONPATH ile.
cat > /usr/local/bin/pvctl <<EOF
#!/usr/bin/env bash
exec env PYTHONPATH="${APP_DIR}" python3 -m procvigil ctl -c "${CONFIG_FILE}" "\$@"
EOF
chmod +x /usr/local/bin/pvctl

echo ">> 'procvigil' grubu oluşturuluyor (sudo'suz pvctl erişimi için)..."
groupadd -f procvigil

echo ">> Dizinler oluşturuluyor..."
mkdir -p "${CONFIG_DIR}" "${LOG_DIR}"

if [[ ! -f "${CONFIG_FILE}" ]]; then
  echo ">> Örnek config yerleştiriliyor: ${CONFIG_FILE}"
  cp "${PROJECT_DIR}/config/procvigil.json" "${CONFIG_FILE}"
else
  echo ">> Mevcut config korunuyor: ${CONFIG_FILE}"
fi

echo ">> systemd unit yerleştiriliyor: ${SERVICE_FILE}"
cp "${PROJECT_DIR}/systemd/procvigil.service" "${SERVICE_FILE}"

echo ">> Config doğrulanıyor..."
PYTHONPATH="${APP_DIR}" python3 -m procvigil check -c "${CONFIG_FILE}" || {
  echo "UYARI: Config doğrulaması başarısız. Lütfen ${CONFIG_FILE} dosyasını düzenleyin." >&2
}

echo ">> systemd yeniden yükleniyor ve servis etkinleştiriliyor..."
systemctl daemon-reload
systemctl enable procvigil.service

cat <<EOF

============================================================
ProcVigil kuruldu (venv/pip yok — saf stdlib).

  Config:   ${CONFIG_FILE}
  Loglar:   ${LOG_DIR}/  (program çıktıları)
  Daemon:   journalctl -u procvigil -f

Sık kullanılan komutlar:
  sudo systemctl start procvigil       # başlat
  sudo systemctl status procvigil      # durum
  sudo systemctl reload procvigil      # config reload (SIGHUP)
  sudo systemctl restart procvigil     # tamamen yeniden başlat
  sudo systemctl stop procvigil        # durdur

Canlı program yönetimi (supervisorctl muadili):
  pvctl status                  # tüm program durumları
  pvctl restart <program>       # tek programı yeniden başlat
  pvctl tail <program> -n 50    # son loglar

Bir kullanıcının sudo'suz pvctl kullanması için 'procvigil' grubuna ekle:
  sudo usermod -aG procvigil <kullanici>   # ardından yeniden oturum açmalı

Config'i düzenledikten sonra:  sudo systemctl reload procvigil  (veya: pvctl reload)
============================================================
EOF
