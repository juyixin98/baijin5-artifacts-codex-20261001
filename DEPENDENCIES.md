# Dependencies

All dependencies are fetched **locally into `third_party/`** by
`scripts/fetch_deps.sh`. No system packages, root access, containers, or
external services are required. Network is used only to download the two
archives below.

## Build toolchain (already provided on this machine)

| Tool | Version | Source |
|------|---------|--------|
| g++ | 13.3 (C++20) | system (`/usr/bin/g++`) |
| GNU make | 4.x | system |
| bash, wget | - | system |

## Vendored dependencies

| Dependency | Version | URL | Purpose |
|-----------|---------|-----|---------|
| CMake | 3.30.5 (linux-x86_64 prebuilt) | https://github.com/Kitware/CMake/releases/download/v3.30.5/cmake-3.30.5-linux-x86_64.tar.gz | build system, extracted to `third_party/cmake` |
| Eigen | 3.4.0 (header only) | https://gitlab.com/libeigen/eigen/-/archive/3.4.0/eigen-3.4.0.tar.gz (GitHub PX4 mirror fallback) | `Eigen::VectorXcd` facade in `src/mathcore/fft_eigen.hpp` |

There are **no other runtime library dependencies**. The FFT core, reference
DFT, contract judge, benchmark and service use only the C++20 standard
library (`<complex>`, `<numbers>`, `<chrono>`, `/proc/self/status` on Linux).

## Why Eigen is vendored

Eigen is a pure header library; the task environment explicitly has no
system Eigen. `CMakeLists.txt` uses `third_party/eigen-src` when present and
falls back to a CMake `FetchContent` URL otherwise.

## No other runtimes

The task requires C++20 only. No Python, .NET, JVM, Node or container runtime
is used by implementation, data generation or tests.
