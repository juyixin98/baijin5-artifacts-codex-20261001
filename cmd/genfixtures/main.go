// Command genfixtures assembles every fixtures/asm/*.asm into a
// versioned .mkobj under fixtures/gen. It is the reproducible fixture
// generator for offline differential runs.
package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"mylnk/internal/objfmt"
	"mylnk/internal/pipeline"
)

func main() {
	root := "."
	if len(os.Args) > 1 {
		root = os.Args[1]
	}
	asmDir := filepath.Join(root, "fixtures", "asm")
	outDir := filepath.Join(root, "fixtures", "gen")
	if err := os.MkdirAll(outDir, 0o755); err != nil {
		fail(err)
	}
	entries, err := os.ReadDir(asmDir)
	if err != nil {
		fail(err)
	}
	n := 0
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".asm") {
			continue
		}
		in := filepath.Join(asmDir, e.Name())
		o, err := pipeline.AssembleFile(in)
		if err != nil {
			fail(fmt.Errorf("%s: %w", in, err))
		}
		b, err := objfmt.Encode(o)
		if err != nil {
			fail(err)
		}
		out := filepath.Join(outDir, strings.TrimSuffix(e.Name(), ".asm")+".mkobj")
		if err := os.WriteFile(out, b, 0o644); err != nil {
			fail(err)
		}
		n++
		fmt.Printf("generated %s (%d bytes) from %s\n", filepath.Base(out), len(b), e.Name())
	}
	fmt.Printf("genfixtures: %d objects written to %s\n", n, outDir)
}

func fail(err error) {
	fmt.Fprintln(os.Stderr, "genfixtures: "+err.Error())
	os.Exit(1)
}
