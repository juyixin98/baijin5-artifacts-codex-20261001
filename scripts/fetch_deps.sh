#!/usr/bin/env bash
# Reproducibly fetch the only two third-party dependencies into third_party/.
# No system packages, no root, no containers.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
TP="$ROOT/third_party"
mkdir -p "$TP"
cd "$TP"

CMAKE_VER=3.30.5
if [ ! -x "$TP/cmake/bin/cmake" ]; then
  echo "[deps] fetching CMake $CMAKE_VER ..."
  wget -q --tries=3 --timeout=60 \
    "https://github.com/Kitware/CMake/releases/download/v${CMAKE_VER}/cmake-${CMAKE_VER}-linux-x86_64.tar.gz" \
    -O cmake.tgz
  tar xzf cmake.tgz
  mv "cmake-${CMAKE_VER}-linux-x86_64" cmake
  rm -f cmake.tgz
else
  echo "[deps] CMake already present"
fi

if [ ! -f "$TP/eigen-src/Eigen/Dense" ]; then
  echo "[deps] fetching Eigen 3.4.0 ..."
  if ! wget -q --tries=2 --timeout=60 \
       "https://gitlab.com/libeigen/eigen/-/archive/3.4.0/eigen-3.4.0.tar.gz" \
       -O eigen.tgz; then
    echo "[deps] gitlab failed, trying github mirror ..."
    wget -q --tries=2 --timeout=60 \
      "https://github.com/PX4/eigen/archive/refs/tags/3.4.0.tar.gz" \
      -O eigen.tgz
  fi
  mkdir -p eigen-src
  tar xzf eigen.tgz -C eigen-src --strip-components=1
  rm -f eigen.tgz
else
  echo "[deps] Eigen already present"
fi

"$TP/cmake/bin/cmake" --version | head -1
echo "[deps] Eigen: $(ls "$TP/eigen-src/Eigen/Dense")"
