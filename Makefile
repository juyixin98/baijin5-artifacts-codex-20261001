.PHONY: all build test race cover fixtures vet fmt tidy clean smoke

all: build

build:
	go build ./...

# Generate the deterministic synthetic fixture document.
fixtures:
	go run ./cmd/genfixtures

test:
	go test ./...

# Tests with the race detector (used in CI).
race:
	go test -race ./...

# Aggregate coverage including coverage exercised by integration tests.
cover:
	go test -race -coverpkg=./internal/... -coverprofile=cov.out ./...
	go tool cover -func=cov.out | tail -1

vet:
	go vet ./...

fmt:
	gofmt -w $(shell find . -name '*.go' -not -path './.git/*')

tidy:
	go mod tidy

# Start the server on an alternate loopback port and run a couple of probes.
smoke: build
	@rm -f /tmp/coaplab-smoke.db
	@go run ./cmd/coapd -listen 127.0.0.1:15683 -db /tmp/coaplab-smoke.db -quiet & echo $$! > /tmp/coaplab-smoke.pid
	@sleep 1
	@go run ./cmd/coapget -addr 127.0.0.1:15683 get /hello -show
	@go run ./cmd/coapget -addr 127.0.0.1:15683 -szx 0 get /cd/300b
	@kill `cat /tmp/coaplab-smoke.pid` 2>/dev/null || true

clean:
	rm -f coaplab.db cov.out /tmp/coaplab-smoke.db /tmp/coaplab-smoke.pid
