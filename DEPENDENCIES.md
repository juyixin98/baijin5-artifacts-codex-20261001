# Dependency manifest

Everything is fetched locally by `scripts/fetch_deps.sh` with pinned SHA-256.
No containers, no root access, no system package manager are required.

## Build toolchain (already present on the host)

| Tool | Version used | Purpose |
|------|--------------|---------|
| g++ | 13.3 (C++20) | Native compiler (`-std=c++20`) |
| GNU make | 4.x | CMake backend |
| curl, sha256sum | system | Dependency download / verification |

## Vendored artifacts (under `third_party/`, reproducible)

| Component | Version | URL | SHA-256 |
|-----------|---------|-----|---------|
| CMake (prebuilt linux-x86_64) | 3.30.5 | https://github.com/Kitware/CMake/releases/download/v3.30.5/cmake-3.30.5-linux-x86_64.tar.gz | `f747d9b23e1a252a8beafb4ed2bc2ddf78cff7f04a8e4de19f4ff88e9b51dc9d` |
| Eigen (header-only) | 3.4.0 | https://gitlab.com/libeigen/eigen/-/archive/3.4.0/eigen-3.4.0.tar.gz | `8586084f71f9bde545ee7fa6d00288b264a2b7ac3607b974e54d13e7162c1c72` |

## Runtime libraries

Only the C++ standard library and libm are linked. There are **no** third-party
runtime dependencies: JSON rendering and the HTTP server are implemented in the
service layer to avoid pulling additional packages.

## Network policy

Network access is used solely to download the two artifacts above. All
computation, fixtures and services are local (the HTTP server binds to
`127.0.0.1`).
