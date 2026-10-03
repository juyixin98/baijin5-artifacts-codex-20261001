package semdiff_test

import (
	"testing"

	"rlmod/internal/fingerprint"
	"rlmod/internal/frontend"
	"rlmod/internal/ir"
	"rlmod/internal/semdiff"
)

func build(t *testing.T, mods map[string]string, semver string) (*ir.Program, *fingerprint.Report) {
	t.Helper()
	names := make([]string, 0, len(mods))
	for n := range mods {
		names = append(names, n)
	}
	parsed := map[string]*frontend.Program{}
	for _, n := range names {
		p, err := frontend.Parse(mods[n])
		if err != nil {
			t.Fatalf("parse: %v", err)
		}
		parsed[n] = p
	}
	prog, err := ir.Build(parsed, names, semver, fingerprint.SchemaVersion)
	if err != nil {
		t.Fatalf("build: %v", err)
	}
	return prog, fingerprint.Compute(prog)
}

var chainOld = map[string]string{
	"core": "package core\nconst N int = 1\nfn helper(x: int): int { return x + 1 }\nfn Pub(x: int): int { return x + N + helper(x) }\n",
	"app":  "package app\nimport core\nfn Run(): int { return core::Pub(1) }\n",
}

func classOf(rs []semdiff.Change, name string) semdiff.ChangeClass {
	for _, c := range rs {
		if c.Name == name {
			return c.Class
		}
	}
	return "<missing>"
}

func has(rs []string, v string) bool {
	for _, x := range rs {
		if x == v {
			return true
		}
	}
	return false
}

func TestPrivateBodyDoesNotPropagate(t *testing.T) {
	oldP, oldF := build(t, chainOld, "1.0.0")
	newMods := map[string]string{
		"core": "package core\nconst N int = 1\nfn helper(x: int): int { return x + 2 }\nfn Pub(x: int): int { return x + N + helper(x) }\n",
		"app":  "package app\nimport core\nfn Run(): int { return core::Pub(1) }\n",
	}
	newP, newF := build(t, newMods, "1.0.0")
	r := semdiff.Diff(oldP, newP, oldF, newF)
	if got := classOf(r.Changes, "helper"); got != semdiff.ClassPrivateBodyOnly {
		t.Fatalf("helper class = %s", got)
	}
	if has(r.Invalidated, "core.Pub") {
		t.Fatal("name-linked public Pub must not be invalidated by private helper body")
	}
	if has(r.Invalidated, "app.Run") {
		t.Fatal("app.Run must not be invalidated by a private helper body")
	}
	if !has(r.Invalidated, "core.helper") {
		t.Fatal("changed helper itself must recompile")
	}
}

func TestInlineConstPropagatesTransitively(t *testing.T) {
	oldP, oldF := build(t, chainOld, "1.0.0")
	newMods := map[string]string{
		"core": "package core\nconst N int = 9\nfn helper(x: int): int { return x + 1 }\nfn Pub(x: int): int { return x + N + helper(x) }\n",
		"app":  "package app\nimport core\nfn Run(): int { return core::Pub(1) }\n",
	}
	newP, newF := build(t, newMods, "1.0.0")
	r := semdiff.Diff(oldP, newP, oldF, newF)
	if got := classOf(r.Changes, "N"); got != semdiff.ClassInlineConst {
		t.Fatalf("N class = %s", got)
	}
	for _, want := range []string{"core.N", "core.Pub", "app.Run"} {
		if !has(r.Invalidated, want) {
			t.Fatalf("minimal invalidation set missing %s: %v", want, r.Invalidated)
		}
	}
}

func TestGenericBodyPropagatesOnlyUsedInstance(t *testing.T) {
	old := map[string]string{
		"m": "package m\nfn Id[T](v: T): T { return v }\nfn Use(): int { return Id[int](1) }\n",
	}
	new := map[string]string{
		"m": "package m\nfn Id[T](v: T): T { return v + 0 - 0 }\nfn Use(): int { return Id[int](1) }\n",
	}
	oldP, oldF := build(t, old, "1.0.0")
	newP, newF := build(t, new, "1.0.0")
	r := semdiff.Diff(oldP, newP, oldF, newF)
	if got := classOf(r.Changes, "Id[int]"); got != semdiff.ClassGenericBody {
		t.Fatalf("Id[int] class = %s", got)
	}
	if !has(r.Invalidated, "m.Use") {
		t.Fatalf("Use embeds Id[int] and must be invalidated: %v", r.Invalidated)
	}
}

func TestVersionMajorInvalidatesAll(t *testing.T) {
	oldP, oldF := build(t, chainOld, "1.0.0")
	newP, newF := build(t, chainOld, "2.0.0")
	r := semdiff.Diff(oldP, newP, oldF, newF)
	if !r.VersionRejected {
		t.Fatal("major version change must reject reuse")
	}
	if len(r.ReuseAllowed) != 0 {
		t.Fatalf("nothing may be reused across major boundary: %v", r.ReuseAllowed)
	}
}

func TestSameVersionNoChangeAllReused(t *testing.T) {
	oldP, oldF := build(t, chainOld, "1.4.2")
	newP, newF := build(t, chainOld, "1.4.2")
	r := semdiff.Diff(oldP, newP, oldF, newF)
	if r.VersionRejected || len(r.Changes) != 0 || len(r.Invalidated) != 0 {
		t.Fatalf("identical builds must be fully reusable: %+v", r)
	}
}
