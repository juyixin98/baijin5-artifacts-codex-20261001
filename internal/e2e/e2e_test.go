// Package e2e runs the committed hand-authored fixture set against the
// real on-disk expectation table. Expected answers live in
// fixtures/expectations.txt and are maintained independently from the
// linker implementation; this test only executes and compares.
package e2e

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"mylnk/internal/frontend"
	"mylnk/internal/logx"
	"mylnk/internal/objfmt"
	"mylnk/internal/semdiff"
)

func repoRoot(t *testing.T) string {
	t.Helper()
	dir, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	for {
		if _, err := os.Stat(filepath.Join(dir, "fixtures", "expectations.txt")); err == nil {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			t.Fatal("repository root with fixtures/ not found")
		}
		dir = parent
	}
}

func TestCommittedExpectationTable(t *testing.T) {
	root := repoRoot(t)
	specBytes, err := os.ReadFile(filepath.Join(root, "fixtures", "expectations.txt"))
	if err != nil {
		t.Fatal(err)
	}
	spec, err := semdiff.ParseSpec(bytes.NewReader(specBytes))
	if err != nil {
		t.Fatalf("parse spec: %v", err)
	}
	if len(spec.Cases) < 8 {
		t.Fatalf("expected at least 8 hand-authored cases, got %d", len(spec.Cases))
	}

	loader := func(name string) (*objfmt.Object, error) {
		base := strings.TrimSuffix(name, ".mkobj")
		path := filepath.Join(root, "fixtures", "asm", base+".asm")
		f, err := os.Open(path)
		if err != nil {
			return nil, err
		}
		defer f.Close()
		return frontend.Assemble(f, base+".o")
	}

	failed := 0
	for _, c := range spec.Cases {
		t.Run(c.ID, func(t *testing.T) {
			var logBuf bytes.Buffer
			log := logx.New(&logBuf, "e2e:"+c.ID, logx.LevelInfo)
			log.Info("mylnk e2e identity=%s objects=%v entry=%s", c.ID, c.Objects, c.Entry)
			rep := semdiff.RunCase(c, loader, log)
			t.Logf("\n%s", logBuf.String())
			if !rep.Passed {
				t.Fatalf("%s: %v", c.ID, rep.Mismatch)
			}
		})
	}
	if failed > 0 {
		t.Fatalf("%d cases failed", failed)
	}
}

func TestPrebuiltObjectsMatchSpec(t *testing.T) {
	root := repoRoot(t)
	specBytes, err := os.ReadFile(filepath.Join(root, "fixtures", "expectations.txt"))
	if err != nil {
		t.Fatal(err)
	}
	spec, err := semdiff.ParseSpec(bytes.NewReader(specBytes))
	if err != nil {
		t.Fatal(err)
	}
	loader := func(name string) (*objfmt.Object, error) {
		b, err := os.ReadFile(filepath.Join(root, "fixtures", "gen", name))
		if err != nil {
			return nil, err
		}
		return objfmt.Decode(b)
	}
	for _, c := range spec.Cases {
		t.Run("prebuilt/"+c.ID, func(t *testing.T) {
			log := logx.Nop()
			rep := semdiff.RunCase(c, loader, log)
			if !rep.Passed {
				t.Fatalf("%s: %v", c.ID, rep.Mismatch)
			}
		})
	}
}
