#!/usr/bin/env bash
# Fetch pinned, local-only build dependencies. No containers, no system install.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEPS="$ROOT/deps"
mkdir -p "$DEPS"

CMAKE_VER="3.28.3"
CMAKE_DIR="cmake-${CMAKE_VER}-linux-x86_64"
CMAKE_URL="https://github.com/Kitware/CMake/releases/download/v${CMAKE_VER}/${CMAKE_DIR}.tar.gz"
CMAKE_SHA="804d231460ab3c8b556a42d2660af4ac7a0e21c98a7f8ee3318a74b4a9a187a6"

EIGEN_VER="3.4.0"
EIGEN_DIR="eigen-${EIGEN_VER}"
EIGEN_URL="https://gitlab.com/libeigen/eigen/-/archive/${EIGEN_VER}/${EIGEN_DIR}.tar.gz"
EIGEN_SHA="8586084f71f9bde545ee7fa6d00288b264a2b7ac3607b974e54d13e7162c1c72"

fetch() {
  local url="$1" sha="$2" dir="$3"
  if [ -d "$DEPS/$dir" ]; then
    echo "== $dir already present"
    return
  fi
  local tmp="$DEPS/${dir}.tar.gz"
  echo "== downloading $url"
  curl -fL --retry 3 -o "$tmp" "$url"
  echo "$sha  $tmp" | sha256sum -c -
  tar xzf "$tmp" -C "$DEPS"
  rm -f "$tmp"
  echo "== extracted $dir"
}

fetch "$CMAKE_URL" "$CMAKE_SHA" "$CMAKE_DIR"
fetch "$EIGEN_URL" "$EIGEN_SHA" "$EIGEN_DIR"
echo "dependencies ready under $DEPS"
