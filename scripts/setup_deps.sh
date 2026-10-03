#!/usr/bin/env bash
# Reproducible, rootless, native dependency installer.
#   * CMake 3.28.3 : official Ubuntu noble .deb packages extracted in-tree
#   * Eigen 3.4.0  : official release tarball (header-only)
#   * Boost 1.86.0 : sparse header-only subset for Boost.Multiprecision/cpp_int
# Every archive is sha256-checked against deps.lock.json (values duplicated
# here deliberately to avoid a JSON parser / extra runtime).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DL="$ROOT/dl"
DEB="$DL/deb"
mkdir -p "$DL" "$DEB" "$ROOT/tools/bin" "$ROOT/third_party"

sha_ok() { # file expected
  local file="$1" expected="$2"
  [ -s "$file" ] || return 1
  local got; got="$(sha256sum "$file" | awk '{print $1}')"
  [ "$got" = "$expected" ]
}

# ---------- CMake via locally extracted .deb (no sudo) ----------
setup_cmake() {
  if "$ROOT/tools/bin/cmake" --version >/dev/null 2>&1; then
    echo "[cmake] present: $("$ROOT/tools/bin/cmake" --version | head -1)"; return
  fi
  declare -A PKGS=(
    [cmake_3.28.3-1build7_amd64.deb]="4b0a7f8c0daf27b26b46997d994ae5d1ee7a3d11dfcda9f7627bb1462c162295"
    [cmake-data_3.28.3-1build7_all.deb]="20a3b644211ce82f35c24f1f5052199adeb0ac159978a8740fd6c0959611557f"
    [librhash0_1.4.3-3build1_amd64.deb]="e9ee69963ff1a56378b9c5ffdd21ea0feaec9647c522349b7612b25103490528"
    [libjsoncpp25_1.9.5-6build1_amd64.deb]="8efea5b75952f3ad1c40e6b5b31de687c297ea29f87f8b63efebb55e13467211"
  )
  # .deb names returned by apt-get download may carry arch suffixes; download
  # by package name and locate the resulting file.
  for pkg in cmake cmake-data librhash0 libjsoncpp25; do
    if ! ls "$DEB"/${pkg}_*.deb >/dev/null 2>&1; then
      echo "[cmake] apt-get download $pkg"
      (cd "$DEB" && apt-get download "$pkg")
    fi
  done
  local f
  for f in "$DEB"/*.deb; do
    local name; name="$(basename "$f")"
    local want="${PKGS[$name]:-}"
    if [ -z "$want" ]; then echo "[cmake] unexpected deb $name" >&2; exit 2; fi
    sha_ok "$f" "$want" || { echo "[cmake] checksum failed: $name" >&2; exit 2; }
    dpkg-deb -x "$f" "$ROOT/tools/cmake"
  done
  cat > "$ROOT/tools/bin/cmake" <<'W'
#!/usr/bin/env bash
HERE="$(cd "$(dirname "$0")/.." && pwd)"
export LD_LIBRARY_PATH="$HERE/cmake/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$HERE/cmake/usr/bin/cmake" "$@"
W
  cat > "$ROOT/tools/bin/ctest" <<'W'
#!/usr/bin/env bash
HERE="$(cd "$(dirname "$0")/.." && pwd)"
export LD_LIBRARY_PATH="$HERE/cmake/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$HERE/cmake/usr/bin/ctest" "$@"
W
  chmod +x "$ROOT/tools/bin/cmake" "$ROOT/tools/bin/ctest"
  echo "[cmake] $("$ROOT/tools/bin/cmake" --version | head -1)"
}

# ---------- Eigen ----------
setup_eigen() {
  if [ -f "$ROOT/third_party/eigen/Eigen/Dense" ]; then echo "[eigen] present"; return; fi
  local f="$DL/eigen-3.4.0.tar.gz"
  local want="8586084f71f9bde545ee7fa6d00288b264a2b7ac3607b974e54d13e7162c1c72"
  sha_ok "$f" "$want" || curl -fSL --retry 3 -o "$f" \
    "https://gitlab.com/libeigen/eigen/-/archive/3.4.0/eigen-3.4.0.tar.gz"
  sha_ok "$f" "$want" || { echo "[eigen] checksum failed" >&2; exit 2; }
  mkdir -p "$DL/_eigen" && tar xzf "$f" -C "$DL/_eigen"
  mv "$DL/_eigen/eigen-3.4.0" "$ROOT/third_party/eigen"
  echo "[eigen] 3.4.0 ready"
}

# ---------- Boost sparse headers ----------
setup_boost() {
  if [ -f "$ROOT/third_party/boost/boost/multiprecision/cpp_int.hpp" ] \
     && g++ -std=c++20 -I"$ROOT/third_party/boost" -x c++ - \
          -o "$DL/.boost_probe" <<<'#include <boost/multiprecision/cpp_int.hpp>
int main(){boost::multiprecision::cpp_int x=7; return x==7?0:1;}' 2>>"$DL/boost_setup.log"; then
    echo "[boost] present"; return
  fi
  # repo:sha256 (pinned at boost-1.86.0)
  local repos="multiprecision:3db5628fcc3fd2afc6f8c634ab151259f86902bff80af1fe328b270a0e9a84ef
assert:a62ec2074cc389dda32595ec7a9f4af68e658c70ed9c1ac32b467a48b176116d
config:bfed17bec038b8e94f5f18d13af9e7200548fb855fa33f02e41d220f5cf5b41a
core:54154ffc20f8cfccc14bf7866b199ccd4786d32ba35ed3e151454575b89d09f7
integer:a31a1b7eebc2d279484ef000fecaa34715f18dad150d5d70af0a6092cf4ee7f1
predef:54659a4ffec272b8edd6a130b49e427968be6b92daed272c059b517e1453219a
static_assert:751d8b84e3602a2a62f2ed473c8089c6e773388152b72591d9e34a3b4a0c7551
throw_exception:d1f238c2a3cc9185208fe11a9d5962c0f5e431dc1dc9d49335ca10fc67616cf0
type_traits:13e9eeaa5fb31459af7a00a2e0be48d2eff9b51e7ba6f45a87da239086f3d428"
  mkdir -p "$DL/boost_repos" "$ROOT/third_party/boost"
  while IFS=: read -r repo want; do
    [ -z "$repo" ] && continue
    local f="$DL/boost_repos/$repo.tar.gz"
    if ! sha_ok "$f" "$want"; then
      echo "[boost] fetch $repo"
      curl -fSL --retry 3 -o "$f" \
        "https://codeload.github.com/boostorg/$repo/tar.gz/refs/tags/boost-1.86.0"
    fi
    sha_ok "$f" "$want" || { echo "[boost] checksum failed: $repo" >&2; exit 2; }
    mkdir -p "$DL/boost_repos/$repo"
    tar xzf "$f" -C "$DL/boost_repos/$repo" --strip-components=1
    cp -a "$DL/boost_repos/$repo/include/boost/." "$ROOT/third_party/boost/boost/"
  done <<< "$repos"
  echo "[boost] sparse 1.86.0 headers ready"
}

setup_cmake
setup_eigen
setup_boost
echo "dependencies ready"
