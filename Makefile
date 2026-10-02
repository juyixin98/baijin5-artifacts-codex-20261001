# stunlab Makefile — all targets work fully offline (deps are vendored).

GO        ?= go
PYTHON    ?= python3
GOFLAGS   := -mod=vendor
OUT_DIR   := evidence/runs/manual-$(shell date -u +%Y%m%dT%H%M%SZ)

.PHONY: all build test race cover fixtures demo clean vet fmt check

all: build

build:
	CGO_ENABLED=1 $(GO) build $(GOFLAGS) -o bin/stund ./cmd/stund
	CGO_ENABLED=1 $(GO) build $(GOFLAGS) -o bin/stunc ./cmd/stunc

# Regenerate the frozen known-answer fixtures with the INDEPENDENT Python
# oracle. Run this before the golden Go tests if the oracle changes.
fixtures:
	$(PYTHON) test/oracle/stun_oracle.py fixtures test/testdata/fixtures.json

vet:
	$(GO) vet $(GOFLAGS) ./...

fmt:
	gofmt -l -w .

# Unit + interop tests (starts a Python 3 subprocess; requires loopback UDP).
test:
	CGO_ENABLED=1 $(GO) test $(GOFLAGS) ./...

race:
	CGO_ENABLED=1 $(GO) test $(GOFLAGS) -race ./...

cover:
	@mkdir -p evidence
	CGO_ENABLED=1 $(GO) test $(GOFLAGS) -race -coverprofile=evidence/coverage.out ./...
	$(GO) tool cover -func=evidence/coverage.out | tail -1

# Real end-to-end run over loopback with normal + abnormal paths and evidence.
demo: build
	./scripts/run_demo.sh

# Full verification gate used for reproduction.
check: fixtures vet race cover
	@echo "check complete"

clean:
	rm -rf bin
