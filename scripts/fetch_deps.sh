#!/usr/bin/env bash
# Fetch project-local, reproducible native dependencies (no containers, no root).
# - CMake: official prebuilt Linux x86_64 binary
# - Eigen: header-only release tarball
# SHA-256 values are pinned; the script refuses to continue on mismatch.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TP="$HERE/third_party"
mkdir -p "$TP"
cd "$TP"

CMAKE_VER="3.30.5"
CMAKE_URL="https://github.com/Kitware/CMake/releases/download/v${CMAKE_VER}/cmake-${CMAKE_VER}-linux-x86_64.tar.gz"
CMAKE_SHA="f747d9b23e1a252a8beafb4ed2bc2ddf78cff7f04a8e4de19f4ff88e9b51dc9d"

EIGEN_VER="3.4.0"
EIGEN_URL="https://gitlab.com/libeigen/eigen/-/archive/${EIGEN_VER}/eigen-${EIGEN_VER}.tar.gz"
EIGEN_SHA="8586084f71f9bde545ee7fa6d00288b264a2b7ac3607b974e54d13e7162c1c72"

fetch() {
  local url="$1" out="$2" sha="$3"
  if [[ -f "$out" ]]; then
    echo "[deps] $out already present, verifying"
  else
    echo "[deps] downloading $url"
    curl -fsSL -o "$out" "$url"
  fi
  local got
  got="$(sha256sum "$out" | awk '{print $1}')"
  if [[ "$got" != "$sha" ]]; then
    echo "[deps] SHA-256 mismatch for $out" >&2
    echo "[deps]   expected $sha" >&2
    echo "[deps]   got      $got" >&2
    exit 1
  fi
  echo "[deps] sha256 ok: $out"
}

fetch "$CMAKE_URL" "cmake.tar.gz" "$CMAKE_SHA"
fetch "$EIGEN_URL" "eigen.tar.gz" "$EIGEN_SHA"

[[ -x "$TP/cmake-${CMAKE_VER}-linux-x86_64/bin/cmake" ]] || tar xzf cmake.tar.gz
[[ -f "$TP/eigen-${EIGEN_VER}/Eigen/Dense" ]] || tar xzf eigen.tar.gz

echo "[deps] cmake: $("$TP/cmake-${CMAKE_VER}-linux-x86_64/bin/cmake" --version | head -1)"
echo "[deps] eigen: $TP/eigen-${EIGEN_VER} (header-only)"
