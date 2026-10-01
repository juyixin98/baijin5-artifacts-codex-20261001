# Dependencies

All dependencies are fetched into the project tree (`./cmake/`) and run as
native Linux x86_64 binaries / headers. No containers, no root, no system
package manager, and no extra language runtimes are required.

| Component | Version | Type | Local path | Upstream |
|-----------|---------|------|------------|----------|
| CMake | 3.30.5 | prebuilt native binary | `cmake/cmake-3.30.5-linux-x86_64/` | github.com/Kitware/CMake releases |
| Eigen | 3.4.0 | header-only library | `cmake/eigen-3.4.0/` (+ `unsupported/Eigen/FFT`) | gitlab.com/libeigen/eigen |
| g++ / make | system (C++20) | build toolchain | `/usr/bin/g++14`, `/usr/bin/make` | preinstalled |

Eigen is used only as an *independent third-party reference/cross-check* in
tests and benchmarks, never by the FFT kernel itself.

Install with: `scripts/setup_deps.sh`.
Network is used solely to download the two archives above.
