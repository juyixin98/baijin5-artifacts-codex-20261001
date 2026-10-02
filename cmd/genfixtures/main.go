// Command genfixtures creates the local synthetic fixture document
// (test/fixtures/data/resources.json). All bodies are generated from a
// deterministic formula — run it again and the bytes are identical.
package main

import (
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"

	"coaplab/internal/fixtures"
)

func main() {
	out := flag.String("out", "test/fixtures/data/resources.json", "fixture file path")
	flag.Parse()

	sizes := map[string]int{
		"hello":    12, // "Hello, CoAP!"
		"cd/16b":   16, // one exact 16-byte block
		"cd/17b":   17, // 16 + 1
		"cd/300b":  300,
		"cd/1kb":   1024,
		"cd/1025b": 1025,
		"cd/3kb":   3000,
		"cd/3073b": 3073, // 3x1024 + 1: forces 4 blocks at max size
	}

	doc := fixtures.File{
		GeneratedBy: "cmd/genfixtures",
		Description: "Deterministic synthetic CoAP resources for local block-wise tests (no real data).",
	}
	doc.Resources = append(doc.Resources, fixtures.ResourceFixture{
		Path:          "hello",
		ContentFormat: 0,
		BodyHex:       hex.EncodeToString([]byte("Hello, CoAP!")),
		Note:          "fixed ASCII greeting",
	})
	for _, p := range []string{"cd/16b", "cd/17b", "cd/300b", "cd/1kb", "cd/1025b", "cd/3kb", "cd/3073b"} {
		body := fixtures.DeterministicBody("coaplab/"+p, sizes[p])
		doc.Resources = append(doc.Resources, fixtures.ResourceFixture{
			Path:          p,
			ContentFormat: 0,
			BodyHex:       hex.EncodeToString(body),
			Note:          fmt.Sprintf("sha256-chain(seed, counter) truncated to %d bytes", sizes[p]),
		})
	}
	doc.Resources = append(doc.Resources, fixtures.ResourceFixture{
		Path: "empty", ContentFormat: 0, BodyHex: "", Note: "zero-length representation",
	})

	if err := os.MkdirAll(filepath.Dir(*out), 0o755); err != nil {
		fatal(err)
	}
	raw, err := json.MarshalIndent(doc, "", "  ")
	if err != nil {
		fatal(err)
	}
	if err := os.WriteFile(*out, append(raw, '\n'), 0o644); err != nil {
		fatal(err)
	}
	fmt.Printf("wrote %d resources to %s\n", len(doc.Resources), *out)
}

func fatal(err error) {
	fmt.Fprintln(os.Stderr, "genfixtures:", err)
	os.Exit(1)
}
