$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "../..")
$Build = Join-Path $Root "build"
& (Join-Path $Build "gen_fixtures") (Join-Path $Root "data")
& (Join-Path $Build "pade_cli") solve --file (Join-Path $Root "data/exp12.series") --m 4 --n 4
& (Join-Path $Build "pade_cli") solve --file (Join-Path $Root "data/x3_factor5.series") --m 1 --n 2
& (Join-Path $Build "pade_cli") eval  --file (Join-Path $Root "data/geometric10.series") --m 1 --n 1 --x 1
