#!/usr/bin/env bash
# Reproducible local dependency provisioning (no root, no containers).
# Downloads CMake (prebuilt) and Eigen (header-only) into the project tree.
set -euo pipefail
cd "$(dirname "$0")/.."

CMAKE_VER=3.30.5
CMAKE_DIR=".toolchain/cmake-${CMAKE_VER}-linux-x86_64"
CMAKE_TGZ="/tmp/cmake-${CMAKE_VER}-linux-x86_64.tar.gz"
CMAKE_URL="https://github.com/Kitware/CMake/releases/download/v${CMAKE_VER}/cmake-${CMAKE_VER}-linux-x86_64.tar.gz"
CMAKE_SHA256="f747d9b23e1a252a8beafb4ed2bc2ddf78cff7f04a8e4de19f4ff88e9b51dc9d"

EIGEN_VER=3.4.0
EIGEN_DIR="third_party/eigen-${EIGEN_VER}"
EIGEN_TGZ="/tmp/eigen-${EIGEN_VER}.tar.gz"
EIGEN_URL="https://gitlab.com/libeigen/eigen/-/archive/${EIGEN_VER}/eigen-${EIGEN_VER}.tar.gz"
EIGEN_SHA256="8586084f71f9bde545ee7fa6d00288b264a2b7ac3607b974e54d13e7162c1c72"

download() { # url out
  if command -v curl >/dev/null 2>&1; then curl -fsSL "$1" -o "$2"; else wget -q "$1" -O "$2"; fi
}

check_sha() { # expected file
  local actual
  actual="$(sha256sum "$2" | awk '{print $1}')"
  if [[ "$actual" != "$1" ]]; then
    echo "sha256 mismatch for $2: $actual != $1" >&2
    exit 1
  fi
}

if [[ ! -x "${CMAKE_DIR}/bin/cmake" ]]; then
  echo "[deps] downloading CMake ${CMAKE_VER} ..."
  download "${CMAKE_URL}" "${CMAKE_TGZ}"
  check_sha "${CMAKE_SHA256}" "${CMAKE_TGZ}"
  mkdir -p .toolchain
  tar -xzf "${CMAKE_TGZ}" -C .toolchain
  rm -f "${CMAKE_TGZ}"
else
  echo "[deps] CMake ${CMAKE_VER} already present"
fi

if [[ ! -f "${EIGEN_DIR}/Eigen/Core" ]]; then
  echo "[deps] downloading Eigen ${EIGEN_VER} ..."
  download "${EIGEN_URL}" "${EIGEN_TGZ}"
  check_sha "${EIGEN_SHA256}" "${EIGEN_TGZ}"
  mkdir -p third_party
  tar -xzf "${EIGEN_TGZ}" -C third_party
  rm -f "${EIGEN_TGZ}"
else
  echo "[Eigen] ${EIGEN_VER} already present"
fi
echo "[deps] done"
