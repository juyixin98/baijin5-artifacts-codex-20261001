package tests

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"pmd/diff"
	"pmd/runtime"
)

// The decision tree engine and the sequential reference interpreter must
// agree on exhaustive small values plus a deterministic random sample.
func TestDiffEnginesAgreeOnCorpus(t *testing.T) {
	programs := []string{
		"programs/list.pmd",
		"programs/tree.pmd",
		"programs/guards.pmd",
	}
	for _, rel := range programs {
		t.Run(rel, func(t *testing.T) {
			prog := loadProgram(t, rel)
			values := diff.Enumerate(prog.Ctors, prog.CtorOrder, 3, 2000)
			values = append(values, diff.Random(prog.Ctors, prog.CtorOrder, 42, 300, 5)...)
			rep := diff.Run(prog, values)
			if len(rep.Uncertain) != 0 {
				t.Errorf("uncertain values (should be none for generated corpus): %+v",
					rep.Uncertain[:min(3, len(rep.Uncertain))])
			}
			if !rep.OK() {
				for _, m := range rep.Mismatches[:min(5, len(rep.Mismatches))] {
					t.Errorf("mismatch on %s: %s\n  tree: %s\n  seq:  %s",
						m.Value, m.Reason, m.Tree, m.Seq)
				}
				t.Fatalf("%d mismatches out of %d compared", len(rep.Mismatches), rep.Compared)
			}
			if rep.Compared < 500 {
				t.Errorf("compared = %d, want a corpus of at least 500", rep.Compared)
			}
			if rep.Equal != rep.Compared {
				t.Errorf("equal = %d, compared = %d", rep.Equal, rep.Compared)
			}
		})
	}
}

// Golden fixtures are hand-written; the engine must reproduce them.
func TestGoldenFixtures(t *testing.T) {
	files, err := filepath.Glob(filepath.Join("..", "fixtures", "golden", "*.json"))
	if err != nil || len(files) == 0 {
		t.Fatalf("no golden files found: %v", err)
	}
	for _, f := range files {
		t.Run(filepath.Base(f), func(t *testing.T) {
			data, err := os.ReadFile(f)
			if err != nil {
				t.Fatalf("read %s: %v", f, err)
			}
			var doc struct {
				Program string            `json:"program"`
				Cases   []diff.GoldenCase `json:"cases"`
			}
			if err := json.Unmarshal(data, &doc); err != nil {
				t.Fatalf("parse %s: %v", f, err)
			}
			prog := loadProgram(t, doc.Program)
			problems := diff.CheckGolden(prog, doc.Cases)
			for _, p := range problems {
				t.Errorf("%s", p)
			}
		})
	}
}

// The diff report must list uncertain cases separately from mismatches.
func TestDiffReportsUncertainSeparately(t *testing.T) {
	prog := loadProgram(t, "programs/list.pmd")
	good := cons(lit(1), lit(2)) // Cons(1, 2): valid, no_match
	bad := ctor("Cons", lit(1))  // arity violation: invalid
	rep := diff.Run(prog, []*runtime.Value{good, bad})
	if len(rep.Uncertain) != 1 {
		t.Fatalf("uncertain = %+v, want exactly 1", rep.Uncertain)
	}
	if rep.Compared != 1 || rep.Equal != 1 {
		t.Errorf("compared = %d, equal = %d, want 1/1", rep.Compared, rep.Equal)
	}
	if !rep.OK() {
		t.Errorf("mismatches = %+v", rep.Mismatches)
	}
}
