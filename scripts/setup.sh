#!/usr/bin/env bash
# Reproducible local toolchain bootstrap. No containers, no system installs,
# no root. Downloads only build dependencies (CMake + Eigen); checksums pinned.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCAL_DIR="$ROOT/.local"
DEPS_DIR="$ROOT/deps"
mkdir -p "$LOCAL_DIR" "$DEPS_DIR"

CMAKE_VER="3.30.5"
CMAKE_TGZ="cmake-${CMAKE_VER}-linux-x86_64.tar.gz"
CMAKE_URL="https://github.com/Kitware/CMake/releases/download/v${CMAKE_VER}/${CMAKE_TGZ}"
CMAKE_SHA="f747d9b23e1a252a8beafb4ed2bc2ddf78cff7f04a8e4de19f4ff88e9b51dc9d"

EIGEN_VER="3.4.0"
EIGEN_TGZ="eigen-${EIGEN_VER}.tar.gz"
EIGEN_URL="https://gitlab.com/libeigen/eigen/-/archive/${EIGEN_VER}/${EIGEN_TGZ}"
EIGEN_SHA="8586084f71f9bde545ee7fa6d00288b264a2b7ac3607b974e54d13e7162c1c72"

fetch() {
    local url="$1" dest="$2" sha="$3"
    if [[ -f "$dest" ]] && echo "$sha  $dest" | sha256sum -c - >/dev/null 2>&1; then
        echo "using cached $dest"
        return
    fi
    echo "downloading $url"
    curl -fsSL "$url" -o "$dest.tmp"
    echo "$sha  $dest.tmp" | sha256sum -c -
    mv "$dest.tmp" "$dest"
}

fetch "$CMAKE_URL" "$DEPS_DIR/$CMAKE_TGZ" "$CMAKE_SHA"
fetch "$EIGEN_URL" "$DEPS_DIR/$EIGEN_TGZ" "$EIGEN_SHA"

if [[ ! -x "$LOCAL_DIR/cmake/bin/cmake" ]]; then
    echo "extracting CMake"
    rm -rf "$LOCAL_DIR/cmake" "$LOCAL_DIR/cmake-${CMAKE_VER}-linux-x86_64"
    tar -xzf "$DEPS_DIR/$CMAKE_TGZ" -C "$LOCAL_DIR"
    mv "$LOCAL_DIR/cmake-${CMAKE_VER}-linux-x86_64" "$LOCAL_DIR/cmake"
fi

if [[ ! -f "$LOCAL_DIR/eigen-${EIGEN_VER}/Eigen/Core" ]]; then
    echo "extracting Eigen (header-only)"
    rm -rf "$LOCAL_DIR/eigen-${EIGEN_VER}"
    tar -xzf "$DEPS_DIR/$EIGEN_TGZ" -C "$LOCAL_DIR"
fi

echo
"$LOCAL_DIR/cmake/bin/cmake" --version | head -1
echo "Eigen headers: $LOCAL_DIR/eigen-${EIGEN_VER}/Eigen/Core"
echo
echo "Bootstrap OK. Next: ./scripts/build.sh && ./scripts/test.sh"
