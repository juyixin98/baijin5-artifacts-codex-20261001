PowerShell equivalents of the Shell scripts. On this Linux host use the
scripts/*.sh versions; these are provided for PowerShell-based environments
and only drive the same project-local native binaries (no containers, no
extra runtimes).

  setup-deps.ps1   -> scripts/setup_deps.sh (requires a local cmake/eigen or
                      manual extraction; the .deb flow itself is Linux-only)
  build.ps1        -> scripts/build.sh
  test.ps1         -> scripts/test.sh
  demo.ps1         -> scripts/demo.sh
