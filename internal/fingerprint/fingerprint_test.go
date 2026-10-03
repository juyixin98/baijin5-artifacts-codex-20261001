package fingerprint_test

import (
	"testing"

	"rlmod/internal/fingerprint"
	"rlmod/internal/frontend"
	"rlmod/internal/ir"
)

func mustBuild(t *testing.T, mods map[string]string, semver, schema string) (*ir.Program, *fingerprint.Report) {
	t.Helper()
	names := make([]string, 0, len(mods))
	parsed := map[string]*frontend.Program{}
	for n := range mods {
		names = append(names, n)
	}
	for _, n := range names {
		p, err := frontend.Parse(mods[n])
		if err != nil {
			t.Fatalf("parse: %v", err)
		}
		parsed[n] = p
	}
	prog, err := ir.Build(parsed, names, semver, schema)
	if err != nil {
		t.Fatalf("build: %v", err)
	}
	return prog, fingerprint.Compute(prog)
}

const coreSrc = "package core\nconst N int = 5\nfn Doubler(x: int): int { return x + N }\n"
const appSrc = "package app\nimport core\nfn Run(): int { return core::Doubler(core::N) }\n"

func TestDeterministic(t *testing.T) {
	_, r1 := mustBuild(t, map[string]string{"core": coreSrc, "app": appSrc}, "1.0.0", fingerprint.SchemaVersion)
	_, r2 := mustBuild(t, map[string]string{"core": coreSrc, "app": appSrc}, "1.0.0", fingerprint.SchemaVersion)
	if r1.ProgramHash != r2.ProgramHash {
		t.Fatalf("nondeterministic program hash %s vs %s", r1.ProgramHash, r2.ProgramHash)
	}
	if r1.Modules["core"].PublicHash != r2.Modules["core"].PublicHash {
		t.Fatalf("nondeterministic module hash")
	}
}

func TestConstValueChangesHash(t *testing.T) {
	_, r1 := mustBuild(t, map[string]string{"core": coreSrc}, "1.0.0", fingerprint.SchemaVersion)
	src2 := "package core\nconst N int = 6\nfn Doubler(x: int): int { return x + N }\n"
	_, r2 := mustBuild(t, map[string]string{"core": src2}, "1.0.0", fingerprint.SchemaVersion)
	if r1.Modules["core"].Symbols["N"].Hash == r2.Modules["core"].Symbols["N"].Hash {
		t.Fatal("const hash must change when inlined value changes")
	}
	if r1.Modules["core"].Symbols["Doubler"].Hash == r2.Modules["core"].Symbols["Doubler"].Hash {
		t.Fatal("function inlining const must change when const value changes")
	}
}

func TestPrivateBodyExcludedFromPublicHash(t *testing.T) {
	src1 := "package m\nfn helper(): int { return 1 }\nfn Pub(): int { return helper() }\n"
	src2 := "package m\nfn helper(): int { return 2 }\nfn Pub(): int { return helper() }\n"
	_, r1 := mustBuild(t, map[string]string{"m": src1}, "1.0.0", fingerprint.SchemaVersion)
	_, r2 := mustBuild(t, map[string]string{"m": src2}, "1.0.0", fingerprint.SchemaVersion)
	if r1.Modules["m"].Symbols["helper"].Hash == r2.Modules["m"].Symbols["helper"].Hash {
		t.Fatal("private helper hash should change with its body")
	}
	if r1.Modules["m"].PublicHash != r2.Modules["m"].PublicHash {
		t.Fatal("public hash must not depend on private helper body")
	}
}

func TestGenericInstancesAreDistinct(t *testing.T) {
	src := "package m\nfn Id[T](v: T): T { return v }\n" +
		"fn A(): int { return Id[int](1) }\nfn B(): str { return Id[str](\"x\") }\n"
	_, r := mustBuild(t, map[string]string{"m": src}, "1.0.0", fingerprint.SchemaVersion)
	if r.Modules["m"].Symbols["Id[int]"].Hash == r.Modules["m"].Symbols["Id[str]"].Hash {
		t.Fatal("different monomorphizations must have different fingerprints")
	}
}
