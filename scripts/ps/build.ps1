# Requires: project-local cmake wrapper present (Linux) or $env:PADE_CMAKE
# pointing at a cmake binary, and Eigen under third_party/eigen-3.4.0.
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "../..")
$Build = Join-Path $Root "build"
$CMake = if ($env:PADE_CMAKE) { $env:PADE_CMAKE }
          else { Join-Path $Root "third_party/cmake-bin/cmake" }
& $CMake -S $Root -B $Build -DCMAKE_BUILD_TYPE=Release
& $CMake --build $Build -j $([Environment]::ProcessorCount)
