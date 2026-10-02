$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "../..")
$Build = Join-Path $Root "build"
& (Join-Path $Root "scripts/ps/build.ps1")
$CTest = Join-Path $Root "third_party/cmake-bin/ctest"
& $CTest --test-dir $Build --output-on-failure
