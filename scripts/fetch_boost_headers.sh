#!/usr/bin/env bash
# Sparse, pinned fetch of the header-only Boost subset required by
# Boost.Multiprecision::cpp_int (Boost 1.86.0). No full release, no root.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DL="$ROOT/dl"; SRC="$DL/boost_repos"; INC="$ROOT/third_party/boost"
TAG="boost-1.86.0"
mkdir -p "$SRC" "$INC/boost"

fetch_repo() {
  local name="$1"
  [ -d "$SRC/$name/include" ] && return 0
  echo "  fetching boostorg/$name"
  if ! timeout 120 curl -fsSL -o "$SRC/$name.tar.gz" \
       "https://codeload.github.com/boostorg/$name/tar.gz/refs/tags/$TAG"; then
    echo "  FAILED: $name"; return 1
  fi
  mkdir -p "$SRC/$name"
  tar xzf "$SRC/$name.tar.gz" -C "$SRC/$name" --strip-components=1
}
sync_includes() { for d in "$SRC"/*/; do [ -d "$d/include/boost" ] && cp -a "$d/include/boost/." "$INC/boost/"; done; }

probe() {
  cat > "$DL/cppint_probe.cpp" <<'CPP'
#include <boost/multiprecision/cpp_int.hpp>
#include <iostream>
int main() {
  using namespace boost::multiprecision;
  cpp_int a("123456789012345678901234567890"), b("987654321098765432109876543210");
  cpp_int c = (a * b + (a - b)) / a;
  std::cout << c << " " << (c % cpp_int(7)) << "\n";
}
CPP
  g++ -std=c++20 -I"$INC" "$DL/cppint_probe.cpp" -o "$DL/cppint_probe" 2>"$DL/probe_err.log"
}

# Header first-component -> boostorg repository (extends as iteration reveals more).
map_repo() {
  [ "$1" = "boost" ] && { echo ""; return; };
  case "$1" in
    assert) echo assert;; config) echo config;; core) echo core;;
    throw_exception) echo throw_exception;; predef) echo predef;;
    static_assert) echo static_assert;; type_traits) echo type_traits;;
    integer) echo integer;; integer_traits) echo integer_traits;;
    cstdfloat|math) echo math;; lexical_cast) echo lexical_cast;;
    multiprecision) echo multiprecision;; random) echo random;;
    utility) echo utility;; concept_check) echo concept_check;; io) echo io;;
    smart_ptr) echo smart_ptr;; type_index) echo type_index;; functional) echo functional;;
    function) echo function;; bind) echo bind;; mpl) echo mpl;;
    preprocessor) echo preprocessor;; tuple) echo tuple;; array) echo array;;
    range) echo range;; regex) echo regex;; container_hash) echo container_hash;;
    detail) echo detail;; endian) echo endian;; numeric) echo numeric;;
    system) echo system;; variant) echo variant;; move) echo move;;
    winapi) echo winapi;; align) echo align;; optional) echo optional;;
    conversion) echo conversion;; typeof) echo typeof;; fusion) echo fusion;;
    chrono) echo chrono;; ratio) echo ratio;; iterator) echo iterator;;
    format) echo format;; tokenizer) echo tokenizer;; algorithm) echo algorithm;;
    unordered) echo unordered;; describe) echo describe;; mp11) echo mp11;;
    variant2) echo variant2;; nowide) echo nowide;; pool) echo pool;;
    parameter) echo parameter;; serialization) echo serialization;;
    atomic) echo atomic;; intrusive) echo intrusive;; interprocess) echo interprocess;;
    container) echo container;; any) echo any;; numeric_conversion) echo numeric_conversion;;
    typeof2) echo typeof;; property_tree) echo property_tree;;
    headers) echo core;;
    *) echo "$1";;
  esac
}

for i in $(seq 1 30); do
  sync_includes
  echo "iteration $i: repos=$(ls -1 "$SRC" | wc -l)"
  if probe; then echo "PROBE_COMPILE_OK"; "$DL/cppint_probe" && { echo PROBE_RUN_OK; exit 0; }; fi
  # Extract every boost/...hpp token from error output (works for both include errors and notes).
  missing="$(grep -oE 'boost/[A-Za-z0-9_/]*\.hpp' "$DL/probe_err.log" \
             | sed 's|boost/boost/|boost/|g; s|^boost/||; s|\.hpp$||' \
             | cut -d/ -f1 | sort -u)"
  added=0; unknown=""
  while read -r h; do
    [ -z "$h" ] && continue
    [ "$h" = "boost" ] && continue
    [ "$h" = "boost" ] && continue
    [ -z "$h" ] && continue
    repo="$(map_repo "$h")"
    if [ ! -d "$SRC/$repo/include" ]; then
      if fetch_repo "$repo"; then added=$((added+1)); else unknown="$unknown $h($repo)"; fi
    fi
  done <<< "$missing"
  if [ "$added" -eq 0 ]; then
    echo "NO_PROGRESS unknown=[$unknown]"; grep -m5 'fatal error' "$DL/probe_err.log"; exit 1
  fi
done
echo ITERATION_LIMIT; exit 1
