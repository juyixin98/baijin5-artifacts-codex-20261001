# Vendored test corpus provenance

The JSON files under `corpus/<implementation>/` and `corpus/raw-data/` are a
subset of the public, MIT-licensed interoperability corpus:

    https://github.com/http2jp/hpack-test-case
    module version: v0.0.0-20190531225041-8a1406e7d14b

They are vendored here so the compatibility tests run fully offline and so
reviewers can see exactly which wires/headers are asserted. `LICENSE` is the
upstream license.

Directories are named after the independent HPACK implementation that
produced each `wire` field (nghttp2, python-hpack, node-http2-hpack,
swift-nio-hpack, go-hpack). `raw-data/` holds encoder *input* (headers
only, no wire) and is consumed by the encoder-oracle tests.

To refresh the subset from a populated Go module cache, copy story files
from:

    $(go env GOMODCACHE)/github.com/http2jp/hpack-test-case@v0.0.0-20190531225041-8a1406e7d14b/
