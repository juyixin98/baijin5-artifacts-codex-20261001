# PowerShell launcher (Windows / PowerShell 7). Mirrors build.sh/test.sh using
# the project-local CMake; no containers or extra language runtimes.
param(
    [ValidateSet("build","test","bench")]
    [string]$Task = "build"
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path "deps/cmake-3.30.5-linux-x86_64") -and -not (Test-Path "deps/cmake-3.30.5-windows-x86_64")) {
    throw "Dependencies missing. On Linux/macOS run ./tools/fetch_deps.sh; fetch the same pinned archives on Windows and extract under .\\deps."
}
$cmake = if ($IsWindows) { ".\\deps\\cmake-3.30.5-windows-x86_64\\bin\\cmake.exe" }
         else { "./deps/cmake-3.30.5-linux-x86_64/bin/cmake" }
& $cmake -S . -B build -DCMAKE_BUILD_TYPE="Release"
& $cmake --build build -j ([Environment]::ProcessorCount)
if ($Task -eq "test") { Push-Location build; try { ctest --output-on-failure } finally { Pop-Location } }
if ($Task -eq "bench") {
    New-Item -ItemType Directory -Force bench | Out-Null
    ./build/polybench --domain FIELD --csv bench/bench_field.csv --md bench/complexity_field.md
    ./build/polybench --domain INTEGER --csv bench/bench_integer.csv --md bench/complexity_integer.md
}
