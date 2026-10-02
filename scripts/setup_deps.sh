#!/usr/bin/env bash
# Project-local, reproducible dependency provisioning (no root, no containers).
# CMake : Ubuntu 24.04 official .deb packages extracted under third_party/
#         (cmake 3.28.3; no system install, no apt install).
# Eigen : upstream 3.4.0 header-only tarball under third_party/eigen-3.4.0
#
# Overrides:
#   SYSTEM_CMAKE=1            use cmake/ctest from PATH if available
#   EIGEN_TARBALL=<path>      use a pre-downloaded eigen tarball
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TP="$ROOT/third_party"
mkdir -p "$TP/debs" "$TP/cmake-bin"

EIGEN_VERSION="3.4.0"
EIGEN_DIR="$TP/eigen-${EIGEN_VERSION}"

if [[ "${SYSTEM_CMAKE:-0}" == "1" ]] && command -v cmake >/dev/null 2>&1 && \
   cmake --version | head -1 | awk '{exit !($3 >= "3.20")}'; then
  echo "[setup] using system cmake: $(command -v cmake)"
else
  if [[ ! -x "$TP/cmake-local/usr/bin/cmake" ]]; then
    echo "[setup] downloading cmake .deb packages (Ubuntu noble, no root)"
    pkgs=(cmake cmake-data librhash0 libjsoncpp25)
    (cd "$TP/debs" && apt-get download "${pkgs[@]}")
    for d in "$TP"/debs/*.deb; do dpkg-deb -x "$d" "$TP/cmake-local"; done
    for t in cmake ctest; do
      cat > "$TP/cmake-bin/$t" <<WRAP
#!/usr/bin/env bash
HERE="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
export LD_LIBRARY_PATH="\$HERE/../cmake-local/usr/lib/x86_64-linux-gnu\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
exec "\$HERE/../cmake-local/usr/bin/$t" "\$@"
WRAP
      chmod +x "$TP/cmake-bin/$t"
    done
  fi
  "$TP/cmake-bin/cmake" --version | head -1
fi

if [[ ! -f "$EIGEN_DIR/Eigen/Dense" ]]; then
  TGZ="$TP/eigen-${EIGEN_VERSION}.tar.gz"
  if [[ -n "${EIGEN_TARBALL:-}" ]]; then
    cp "$EIGEN_TARBALL" "$TGZ"
  else
    urls=(
      "https://gitlab.com/libeigen/eigen/-/archive/${EIGEN_VERSION}/eigen-${EIGEN_VERSION}.tar.gz"
      "https://github.com/PX4/eigen/archive/refs/tags/${EIGEN_VERSION}.tar.gz"
    )
    ok=0
    for u in "${urls[@]}"; do
      echo "[setup] GET $u"
      if curl -fL --retry 3 --retry-all-errors --connect-timeout 15 -o "$TGZ.part" "$u"; then
        mv "$TGZ.part" "$TGZ"; ok=1; break
      fi
    done
    [[ $ok -eq 1 ]] || { echo "[setup] eigen download failed" >&2; exit 1; }
  fi
  rm -rf "$EIGEN_DIR" "$TP/eigen-Eigen-${EIGEN_VERSION}"
  tar -xzf "$TGZ" -C "$TP"
  [[ -d "$TP/eigen-Eigen-${EIGEN_VERSION}" ]] && mv "$TP/eigen-Eigen-${EIGEN_VERSION}" "$EIGEN_DIR"
fi
test -f "$EIGEN_DIR/Eigen/Dense"
echo "[setup] cmake: $TP/cmake-bin/cmake"
echo "[setup] eigen: $EIGEN_DIR"
