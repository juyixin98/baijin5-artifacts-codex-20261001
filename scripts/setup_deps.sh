#!/usr/bin/env bash
# Reproducible, project-local dependency bootstrap (no root, no containers).
# Fetches a prebuilt CMake binary and the Eigen header-only library into .deps/.
# Mirrors are tried first; official upstream is the final fallback.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPS_DIR="${ROOT_DIR}/.deps"
DOWNLOAD_DIR="${DEPS_DIR}/downloads"
CMAKE_VERSION="3.30.5"
EIGEN_VERSION="3.4.0"

CMAKE_TARBALL="cmake-${CMAKE_VERSION}-linux-x86_64.tar.gz"
EIGEN_TARBALL="eigen-${EIGEN_VERSION}.tar.gz"

# Each entry: "<url>". gitlab is first for Eigen (reliable); for CMake a
# reachable GitHub release mirror is tried before the slow direct route.
CMAKE_URLS=(
  "https://gh-proxy.com/https://github.com/Kitware/CMake/releases/download/v${CMAKE_VERSION}/${CMAKE_TARBALL}"
  "https://ghfast.top/https://github.com/Kitware/CMake/releases/download/v${CMAKE_VERSION}/${CMAKE_TARBALL}"
  "https://github.com/Kitware/CMake/releases/download/v${CMAKE_VERSION}/${CMAKE_TARBALL}"
  "https://cmake.org/files/v${CMAKE_VERSION%.*}/${CMAKE_TARBALL}"
)
EIGEN_URLS=(
  "https://gitlab.com/libeigen/eigen/-/archive/${EIGEN_VERSION}/${EIGEN_TARBALL}"
  "https://gh-proxy.com/https://gitlab.com/libeigen/eigen/-/archive/${EIGEN_VERSION}/${EIGEN_TARBALL}"
)

mkdir -p "${DOWNLOAD_DIR}"

fetch() {
  local dest="$1"; shift
  local urls=("$@")
  if [[ -f "${dest}.ok" ]]; then
    echo "[setup] cached: ${dest}"
    return
  fi
  local ok=0
  for url in "${urls[@]}"; do
    echo "[setup] trying ${url}"
    if curl -fL --retry 2 --retry-delay 2 --connect-timeout 15 \
            -o "${dest}.tmp" "${url}"; then
      ok=1
      break
    fi
    echo "[setup] failed, trying next mirror"
  done
  if [[ "${ok}" -ne 1 ]]; then
    echo "[setup] all download locations failed for ${dest}" >&2
    exit 1
  fi
  mv "${dest}.tmp" "${dest}"
  touch "${dest}.ok"
}

# --- CMake -------------------------------------------------------------------
CMAKE_ROOT="${DEPS_DIR}/cmake-${CMAKE_VERSION}-linux-x86_64"
if [[ ! -x "${CMAKE_ROOT}/bin/cmake" ]]; then
  fetch "${DOWNLOAD_DIR}/${CMAKE_TARBALL}" "${CMAKE_URLS[@]}"
  echo "[setup] extracting CMake ${CMAKE_VERSION}"
  tar -xzf "${DOWNLOAD_DIR}/${CMAKE_TARBALL}" -C "${DEPS_DIR}"
fi
echo "[setup] CMake: $("${CMAKE_ROOT}/bin/cmake" --version | head -1)"

# --- Eigen -------------------------------------------------------------------
EIGEN_ROOT="${DEPS_DIR}/eigen-${EIGEN_VERSION}"
if [[ ! -f "${EIGEN_ROOT}/Eigen/Core" ]]; then
  fetch "${DOWNLOAD_DIR}/${EIGEN_TARBALL}" "${EIGEN_URLS[@]}"
  echo "[setup] extracting Eigen ${EIGEN_VERSION}"
  tar -xzf "${DOWNLOAD_DIR}/${EIGEN_TARBALL}" -C "${DEPS_DIR}"
fi
echo "[setup] Eigen headers: ${EIGEN_ROOT}/Eigen/Core"

# Record checksums so downstream runs can verify reproducibility.
( cd "${DOWNLOAD_DIR}" && sha256sum "${CMAKE_TARBALL}" "${EIGEN_TARBALL}" ) \
  > "${DEPS_DIR}/SHA256SUMS"
echo "[setup] checksums written to ${DEPS_DIR}/SHA256SUMS"

cat > "${DEPS_DIR}/env.sh" <<ENV
# Source this to put the project-local CMake on PATH.
export PATH="${CMAKE_ROOT}/bin:\$PATH"
export EIGEN_ROOT="${EIGEN_ROOT}"
ENV
echo "[setup] done. Run: source .deps/env.sh"
