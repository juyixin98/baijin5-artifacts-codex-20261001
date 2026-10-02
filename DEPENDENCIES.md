# Dependencies

All dependencies are fetched into `third_party/` by `scripts/setup_deps.sh`.
Nothing is installed system-wide; no root, no containers, no extra language
runtimes are used.

| Component | Version | Source | Kind |
|---|---|---|---|
| Compiler | g++ 13+ (C++20) | system (`/usr/bin/g++`) | build |
| GNU make | any | system | build |
| CMake | 3.28.3 | Ubuntu 24.04 official `.deb` packages via `apt-get download` (no install): `cmake`, `cmake-data`, `librhash0`, `libjsoncpp25` | extracted under `third_party/cmake-local/`, wrapper in `third_party/cmake-bin/` |
| Eigen | 3.4.0 | upstream tarball `gitlab.com/libeigen/eigen` | header-only, under `third_party/eigen-3.4.0/` |

Only the standard C++ library is linked at runtime; there are no external
services or accounts. Test fixtures are generated locally by `gen_fixtures`.

Override knobs:

- `SYSTEM_CMAKE=1 scripts/setup_deps.sh` — use a `cmake` already on `PATH`
  (requires CMake >= 3.20).
- `EIGEN_TARBALL=/path/to/eigen-3.4.0.tar.gz` — use a pre-downloaded archive.

The `.deb` extraction makes the project-local CMake self-contained; its
wrapper sets `LD_LIBRARY_PATH` to the extracted `librhash`/`jsoncpp`
libraries.
