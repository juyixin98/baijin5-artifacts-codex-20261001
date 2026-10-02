#!/usr/bin/env bash
# Fetch pinned, project-local build dependencies. No containers, no system install.
# Usage: scripts/fetch_deps.sh [--emit-checksums]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPS="$ROOT/.deps"
CACHE="$DEPS/cache"
mkdir -p "$CACHE"

# Pinned versions. Checksums are recorded in scripts/dep_checksums.sha256 and
# verified after download (the first bootstrap records them from the fetched
# artifacts on a trusted network; subsequent runs enforce them).
CMAKE_VER="3.30.5"
EIGEN_VER="3.4.0"
BOOST_VER="1.85.0"
CMAKE_URL="https://github.com/Kitware/CMake/releases/download/v${CMAKE_VER}/cmake-${CMAKE_VER}-linux-x86_64.tar.gz"
EIGEN_URL="https://gitlab.com/libeigen/eigen/-/archive/${EIGEN_VER}/eigen-${EIGEN_VER}.tar.gz"
BOOST_URL="https://archives.boost.io/release/${BOOST_VER}/source/boost_${BOOST_VER//./_}.tar.bz2"

CMAKE_TGZ="cmake-${CMAKE_VER}-linux-x86_64.tar.gz"
EIGEN_TGZ="eigen-${EIGEN_VER}.tar.gz"
BOOST_TBZ2="boost_${BOOST_VER//./_}.tar.bz2"

fetch() { # url -> local cached filename
  local url="$1" out="$CACHE/$2"
  if [[ ! -s "$out" ]]; then
    echo "[fetch_deps] downloading $(basename "$out") ..." >&2
    curl -fSL --retry 3 --retry-delay 2 -o "$out.part" "$url"
    mv "$out.part" "$out"
  fi
  printf '%s\n' "$out"
}

fetch "$CMAKE_URL" "$CMAKE_TGZ"
fetch "$EIGEN_URL" "$EIGEN_TGZ"
fetch "$BOOST_URL" "$BOOST_TBZ2"

if [[ "${1:-}" == "--emit-checksums" ]]; then
  ( cd "$CACHE" && sha256sum "$CMAKE_TGZ" "$EIGEN_TGZ" "$BOOST_TBZ2" )
  exit 0
fi

CHK="$ROOT/scripts/dep_checksums.sha256"
if [[ -s "$CHK" ]]; then
  echo "[fetch_deps] verifying pinned checksums ..." >&2
  ( cd "$CACHE" && sha256sum -c "$CHK" )
else
  echo "[fetch_deps] WARNING: $CHK missing; recording checksums of fetched artifacts" >&2
  ( cd "$CACHE" && sha256sum "$CMAKE_TGZ" "$EIGEN_TGZ" "$BOOST_TBZ2" > "$CHK" )
fi

[[ -x "$DEPS/cmake-${CMAKE_VER}-linux-x86_64/bin/cmake" ]] || \
  tar -xzf "$CACHE/$CMAKE_TGZ" -C "$DEPS"
[[ -f "$DEPS/eigen-${EIGEN_VER}/Eigen/Core" ]] || \
  tar -xzf "$CACHE/$EIGEN_TGZ" -C "$DEPS"
[[ -d "$DEPS/boost_${BOOST_VER//./_}/boost/multiprecision" ]] || \
  tar -xjf "$CACHE/$BOOST_TBZ2" -C "$DEPS" \
    "boost_${BOOST_VER//./_}/boost/multiprecision" \
    "boost_${BOOST_VER//./_}/boost/config" \
    "boost_${BOOST_VER//./_}/boost/assert.hpp" \
    "boost_${BOOST_VER//./_}/boost/throw_exception.hpp" \
    "boost_${BOOST_VER//./_}/boost/noncopyable.hpp"
echo "[fetch_deps] ready under $DEPS" >&2
