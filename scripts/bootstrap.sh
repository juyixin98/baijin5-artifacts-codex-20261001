#!/usr/bin/env bash
# Fetches the pinned, project-local toolchain dependencies:
#   tools/cmake/              CMake 3.30.5 prebuilt binary (hash-verified against
#                             Kitware's official SHA-256.txt)
#   third_party/eigen-3.4.0/  Eigen 3.4.0 headers (hash-verified against the
#                             pinned sha256 recorded in third_party/LOCK.md)
# Everything stays inside the project tree; no root, no containers.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS_DIR="$ROOT/tools"
THIRD_PARTY="$ROOT/third_party"

CMAKE_VERSION="3.30.5"
EIGEN_VERSION="3.4.0"
EIGEN_SHA256="8586084f71f9bde545ee7fa6d00288b264a2b7ac3607b974e54d13e7162c1c72"

fetch() { # fetch <url> <dest>
  if command -v curl >/dev/null 2>&1; then
    curl -fL --retry 3 --connect-timeout 20 -o "$2" "$1"
  elif command -v wget >/dev/null 2>&1; then
    wget -O "$2" "$1"
  else
    echo "bootstrap: need curl or wget" >&2
    return 1
  fi
}

mkdir -p "$TOOLS_DIR" "$THIRD_PARTY"

# ---- CMake -----------------------------------------------------------------
if [[ -x "$TOOLS_DIR/cmake/bin/cmake" ]]; then
  echo "bootstrap: cmake already present ($("$TOOLS_DIR/cmake/bin/cmake" --version | head -1))"
else
  pkg="cmake-${CMAKE_VERSION}-linux-x86_64"
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  echo "bootstrap: downloading CMake ${CMAKE_VERSION}"
  fetch "https://github.com/Kitware/CMake/releases/download/v${CMAKE_VERSION}/${pkg}.tar.gz" "$tmp/${pkg}.tar.gz" \
    || fetch "https://cmake.org/files/v3.30/${pkg}.tar.gz" "$tmp/${pkg}.tar.gz"
  fetch "https://github.com/Kitware/CMake/releases/download/v${CMAKE_VERSION}/cmake-${CMAKE_VERSION}-SHA-256.txt" "$tmp/SHA-256.txt" \
    || fetch "https://cmake.org/files/v3.30/cmake-${CMAKE_VERSION}-SHA-256.txt" "$tmp/SHA-256.txt"
  (cd "$tmp" && grep "  ${pkg}\.tar\.gz\$" SHA-256.txt | sha256sum -c -)
  tar -xzf "$tmp/${pkg}.tar.gz" -C "$tmp"
  mv "$tmp/$pkg" "$TOOLS_DIR/cmake"
  rm -rf "$tmp"
  trap - EXIT
  echo "bootstrap: cmake ready ($("$TOOLS_DIR/cmake/bin/cmake" --version | head -1))"
fi

# ---- Eigen -----------------------------------------------------------------
if [[ -f "$THIRD_PARTY/eigen-${EIGEN_VERSION}/Eigen/Dense" ]]; then
  echo "bootstrap: eigen already present (third_party/eigen-${EIGEN_VERSION})"
else
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  echo "bootstrap: downloading Eigen ${EIGEN_VERSION}"
  fetch "https://gitlab.com/libeigen/eigen/-/archive/${EIGEN_VERSION}/eigen-${EIGEN_VERSION}.tar.gz" "$tmp/eigen.tar.gz"
  echo "${EIGEN_SHA256}  $tmp/eigen.tar.gz" | sha256sum -c -
  tar -xzf "$tmp/eigen.tar.gz" -C "$THIRD_PARTY"
  rm -rf "$tmp"
  trap - EXIT
  echo "bootstrap: eigen ready (third_party/eigen-${EIGEN_VERSION})"
fi

echo "bootstrap: done"
