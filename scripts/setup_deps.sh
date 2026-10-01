#!/usr/bin/env bash
# Fetch the two local, user-space dependencies into ./cmake.
# Native host only; no containers, no root, no system package manager.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPS="$HERE/cmake"
mkdir -p "$DEPS"

CMAKE_VER="3.30.5"
CMAKE_DIR="cmake-${CMAKE_VER}-linux-x86_64"
CMAKE_TGZ="${CMAKE_DIR}.tar.gz"
CMAKE_URL="https://github.com/Kitware/CMake/releases/download/v${CMAKE_VER}/${CMAKE_TGZ}"

EIGEN_VER="3.4.0"
EIGEN_DIR="eigen-${EIGEN_VER}"
EIGEN_TGZ="${EIGEN_DIR}.tar.gz"
EIGEN_URL="https://gitlab.com/libeigen/eigen/-/archive/${EIGEN_VER}/${EIGEN_TGZ}"

fetch() { # url out
  local url="$1" out="$2"
  if [[ -s "$out" ]] && gzip -t "$out" 2>/dev/null; then
    echo "have valid $out"; return 0
  fi
  echo "downloading $url"
  wget -q -c "$url" -O "$out"
  gzip -t "$out"
}

cd "$DEPS"
if [[ ! -x "$DEPS/$CMAKE_DIR/bin/cmake" ]]; then
  fetch "$CMAKE_URL" "$CMAKE_TGZ"
  tar xzf "$CMAKE_TGZ"
fi
if [[ ! -f "$DEPS/$EIGEN_DIR/Eigen/Core" ]]; then
  fetch "$EIGEN_URL" "$EIGEN_TGZ"
  tar xzf "$EIGEN_TGZ"
fi

echo "dependencies ready:"
"$DEPS/$CMAKE_DIR/bin/cmake" --version | head -1
echo "eigen: $DEPS/$EIGEN_DIR"
