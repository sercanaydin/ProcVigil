#!/usr/bin/env bash
#
# ProcVigil uzaktan (tek satır) kurulum scripti.
#
# "apt-get install supervisor" benzeri kullanım — sunucuda tek komutla kurar:
#
#   curl -fsSL https://raw.githubusercontent.com/sercanaydin/ProcVigil/main/scripts/remote-install.sh | sudo bash
#
# Depoyu indirir (git varsa clone, yoksa tarball) ve platforma uygun kurulum
# scriptini (Linux: install.sh, macOS: install-macos.sh) çalıştırır.
#
# Özelleştirme (ortam değişkenleri ile):
#   PROCVIGIL_REPO    Depo URL'si (vars: https://github.com/sercanaydin/ProcVigil)
#   PROCVIGIL_BRANCH  Dal (vars: main)
#
set -euo pipefail

REPO="${PROCVIGIL_REPO:-https://github.com/sercanaydin/ProcVigil}"
BRANCH="${PROCVIGIL_BRANCH:-main}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Bu script root ile çalışmalı. Örnek:" >&2
  echo "  curl -fsSL <url>/scripts/remote-install.sh | sudo bash" >&2
  exit 1
fi

echo ">> ProcVigil indiriliyor: ${REPO} (${BRANCH})"
TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT
SRC_DIR=""

if command -v git >/dev/null 2>&1; then
  git clone --depth 1 --branch "${BRANCH}" "${REPO}" "${TMP}/ProcVigil"
  SRC_DIR="${TMP}/ProcVigil"
else
  echo ">> git yok; tarball indiriliyor..."
  if ! command -v curl >/dev/null 2>&1; then
    echo "HATA: git veya curl gerekli." >&2
    exit 1
  fi
  curl -fsSL "${REPO}/archive/refs/heads/${BRANCH}.tar.gz" -o "${TMP}/src.tgz"
  tar -xzf "${TMP}/src.tgz" -C "${TMP}"
  # Tarball "ProcVigil-<branch>" şeklinde açılır.
  SRC_DIR="$(find "${TMP}" -maxdepth 1 -type d -name 'ProcVigil-*' | head -n 1)"
fi

if [[ -z "${SRC_DIR}" || ! -d "${SRC_DIR}" ]]; then
  echo "HATA: kaynak dizin bulunamadı." >&2
  exit 1
fi

cd "${SRC_DIR}"
echo ">> Kaynak: ${SRC_DIR}"

if [[ "$(uname)" == "Darwin" ]]; then
  echo ">> macOS tespit edildi -> scripts/install-macos.sh"
  bash scripts/install-macos.sh
else
  echo ">> Linux tespit edildi -> scripts/install.sh"
  bash scripts/install.sh
fi
