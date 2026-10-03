# Reproducible native verification (PowerShell edition).
# Requires Go 1.22 on PATH. Uses no containers and no extra runtimes.
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$RunId = "verify-$(Get-Date -Format yyyyMMdd-HHmmss)-$PID"
$env:MYLNK_RUN_ID = $RunId

Write-Host "==> run id: $RunId"
Write-Host "==> go version: $(go version)"

go mod verify
go vet ./...
go test ./... -count=1 -race
go run ./cmd/genfixtures . | Out-Null
go run ./cmd/mylnk diff -from-asm -asmdir fixtures/asm -spec fixtures/expectations.txt
go run ./cmd/mylnk diff -from-asm=false -objdir fixtures/gen -spec fixtures/expectations.txt
go run ./cmd/mylnk run -entry main fixtures/gen/ws_main.mkobj fixtures/gen/ws_strong.mkobj fixtures/gen/ws_weak.mkobj
go run ./cmd/mylnk run -entry main fixtures/gen/circ_main.mkobj fixtures/gen/circ_even.mkobj fixtures/gen/circ_odd.mkobj

Write-Host "==> undefined symbol diagnostic (expected non-zero exit)"
try {
    go run ./cmd/mylnk run -entry main fixtures/gen/undef_main.mkobj
    throw "undefined-symbol case unexpectedly succeeded"
} catch {
    Write-Host "    diagnosed as expected (non-zero exit)"
}

go run ./cmd/mylnk link -map -entry main fixtures/gen/ws_main.mkobj fixtures/gen/ws_weak.mkobj fixtures/gen/ws_strong.mkobj
Write-Host "==> ALL VERIFICATION STEPS PASSED ($RunId)"
