package pipeline_test

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"rlmod/internal/fingerprint"
	"rlmod/internal/pipeline"
)

func scenariosDir() string {
	// testdata is repo-root relative to this package's working directory.
	return filepath.Join("..", "..", "testdata", "scenarios")
}

func goldenDir() string {
	return filepath.Join("..", "..", "testdata", "golden")
}

func loadGolden(t *testing.T, name string) map[string]any {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(goldenDir(), name))
	if err != nil {
		t.Fatal(err)
	}
	var m map[string]any
	if err := json.Unmarshal(b, &m); err != nil {
		t.Fatal(err)
	}
	return m
}

func loadBuild(t *testing.T, dir, semver string) *pipeline.Build {
	t.Helper()
	b, err := pipeline.Load(dir, semver, fingerprint.SchemaVersion)
	if err != nil {
		t.Fatalf("load %s: %v", dir, err)
	}
	return b
}

func asStrings(v any) []string {
	var out []string
	for _, x := range v.([]any) {
		out = append(out, x.(string))
	}
	return out
}

func changeClasses(b *pipeline.Build, other *pipeline.Build) map[string]string {
	d := pipeline.Compare(b, other)
	m := map[string]string{}
	for _, c := range d.Changes {
		m[c.Module+"."+c.Name] = string(c.Class)
	}
	return m
}

func TestGoldenPrivateBody(t *testing.T) {
	g := loadGolden(t, "private-body.json")
	oldB := loadBuild(t, filepath.Join(scenariosDir(), "private-body_old"), "1.0.0")
	newB := loadBuild(t, filepath.Join(scenariosDir(), "private-body_new"), "1.0.0")

	ov, err := oldB.Run()
	if err != nil {
		t.Fatal(err)
	}
	nv, err := newB.Run()
	if err != nil {
		t.Fatal(err)
	}
	if ov.Format() != g["run_old"] {
		t.Fatalf("old run = %s want %s", ov.Format(), g["run_old"])
	}
	if nv.Format() != g["run_new"] {
		t.Fatalf("new run = %s want %s", nv.Format(), g["run_new"])
	}

	d := pipeline.Compare(oldB, newB)
	changes := changeClasses(oldB, newB)
	for _, c := range g["changes"].([]any) {
		cm := c.(map[string]any)
		key := cm["module"].(string) + "." + cm["name"].(string)
		if changes[key] != cm["class"] {
			t.Fatalf("change %s = %s want %s", key, changes[key], cm["class"])
		}
	}
	if len(d.Changes) != 1 {
		t.Fatalf("expected exactly 1 change, got %d", len(d.Changes))
	}
	for _, inv := range asStrings(g["invalidated"]) {
		if !contains(d.Invalidated, inv) {
			t.Fatalf("missing invalidated %s in %v", inv, d.Invalidated)
		}
	}
	if len(d.Invalidated) != 1 {
		t.Fatalf("minimal invalidation set must be exactly {core.secret}, got %v", d.Invalidated)
	}
	for _, kept := range asStrings(g["not_invalidated"]) {
		if contains(d.Invalidated, kept) {
			t.Fatalf("%s must stay reusable", kept)
		}
	}
}

func TestGoldenInlineConst(t *testing.T) {
	g := loadGolden(t, "inline-const.json")
	oldB := loadBuild(t, filepath.Join(scenariosDir(), "inline-const_old"), "1.0.0")
	newB := loadBuild(t, filepath.Join(scenariosDir(), "inline-const_new"), "1.0.0")
	ov, _ := oldB.Run()
	nv, _ := newB.Run()
	if ov.Format() != g["run_old"] || nv.Format() != g["run_new"] {
		t.Fatalf("runs = %s,%s want %s,%s", ov.Format(), nv.Format(), g["run_old"], g["run_new"])
	}
	changes := changeClasses(oldB, newB)
	for _, c := range g["changes_include"].([]any) {
		cm := c.(map[string]any)
		key := cm["module"].(string) + "." + cm["name"].(string)
		if changes[key] != cm["class"] {
			t.Fatalf("change %s = %s want %s", key, changes[key], cm["class"])
		}
	}
	d := pipeline.Compare(oldB, newB)
	for _, inv := range asStrings(g["invalidated_include"]) {
		if !contains(d.Invalidated, inv) {
			t.Fatalf("minimal set missing %s: %v", inv, d.Invalidated)
		}
	}
	for _, kept := range asStrings(g["not_invalidated"]) {
		if contains(d.Invalidated, kept) {
			t.Fatalf("%s must stay reusable", kept)
		}
	}
}

func TestGoldenGenericBody(t *testing.T) {
	g := loadGolden(t, "generic-body.json")
	oldB := loadBuild(t, filepath.Join(scenariosDir(), "generic-body_old"), "1.0.0")
	newB := loadBuild(t, filepath.Join(scenariosDir(), "generic-body_new"), "1.0.0")
	ov, _ := oldB.Run()
	nv, _ := newB.Run()
	if ov.Format() != g["run_old"] || nv.Format() != g["run_new"] {
		t.Fatalf("runs = %s,%s", ov.Format(), nv.Format())
	}
	d := pipeline.Compare(oldB, newB)
	for _, inv := range asStrings(g["invalidated_include"]) {
		if !contains(d.Invalidated, inv) {
			t.Fatalf("missing %s: %v", inv, d.Invalidated)
		}
	}
	for _, kept := range asStrings(g["not_invalidated"]) {
		if contains(d.Invalidated, kept) {
			t.Fatalf("%s must stay reusable", kept)
		}
	}
}

func TestGoldenPublicType(t *testing.T) {
	g := loadGolden(t, "public-type.json")
	oldB := loadBuild(t, filepath.Join(scenariosDir(), "public-type_old"), "1.0.0")
	newB := loadBuild(t, filepath.Join(scenariosDir(), "public-type_new"), "1.0.0")
	changes := changeClasses(oldB, newB)
	for _, c := range g["changes_include"].([]any) {
		cm := c.(map[string]any)
		key := cm["module"].(string) + "." + cm["name"].(string)
		if changes[key] != cm["class"] {
			t.Fatalf("change %s = %s want %s", key, changes[key], cm["class"])
		}
	}
	d := pipeline.Compare(oldB, newB)
	for _, inv := range asStrings(g["invalidated_include"]) {
		if !contains(d.Invalidated, inv) {
			t.Fatalf("missing %s: %v", inv, d.Invalidated)
		}
	}
}

func TestGoldenVersionGate(t *testing.T) {
	g := loadGolden(t, "version.json")
	oldB := loadBuild(t, filepath.Join(scenariosDir(), "version-old"), g["old_semver"].(string))
	newB, err := pipeline.Load(filepath.Join(scenariosDir(), "version-new"), g["new_semver"].(string), fingerprint.SchemaVersion)
	if err != nil {
		t.Fatal(err)
	}
	d := pipeline.Compare(oldB, newB)
	if d.VersionRejected != g["version_rejected"].(bool) {
		t.Fatalf("version rejected = %v want %v (%s)", d.VersionRejected, g["version_rejected"], d.VersionReason)
	}
	if len(d.Changes) != int(g["same_source_changes"].(float64)) {
		t.Fatalf("identical sources should produce 0 changes, got %d", len(d.Changes))
	}
	if len(d.ReuseAllowed) != 0 {
		t.Fatalf("across major boundary nothing may be reused: %v", d.ReuseAllowed)
	}
}

func TestCompileFailureCategories(t *testing.T) {
	cases := []struct {
		dir  string
		kind string
	}{
		{"errors", "import_missing"},
		{"errors2", "unknown_symbol"},
		{"errors3", "type_mismatch"},
	}
	for _, tc := range cases {
		t.Run(tc.dir, func(t *testing.T) {
			_, err := pipeline.Load(filepath.Join(scenariosDir(), tc.dir), "1.0.0", fingerprint.SchemaVersion)
			if err == nil {
				t.Fatal("expected compile failure")
			}
			if !strings.Contains(err.Error(), "["+tc.kind+"]") {
				t.Fatalf("error %v does not carry category %s", err, tc.kind)
			}
		})
	}
}

func TestRuntimeFailureCategory(t *testing.T) {
	b := loadBuild(t, filepath.Join(scenariosDir(), "errors4"), "1.0.0")
	_, err := b.Run()
	if err == nil || !strings.Contains(err.Error(), "division_by_zero") {
		t.Fatalf("want division_by_zero runtime error, got %v", err)
	}
}

func TestSensitiveValueMaskedInManifest(t *testing.T) {
	b := loadBuild(t, filepath.Join(scenariosDir(), "sensitive"), "1.0.0")
	out := pipeline.RenderFingerprints(b)
	if strings.Contains(out, "SECRET-1234567890") {
		t.Fatal("sensitive literal leaked into fingerprint manifest")
	}
	if !strings.Contains(out, "<redacted>") {
		t.Fatal("sensitive const should be rendered as <redacted>")
	}
}

func contains(xs []string, v string) bool {
	for _, x := range xs {
		if x == v {
			return true
		}
	}
	return false
}

func TestSensitiveChangeLeaksNowhere(t *testing.T) {
	oldB := loadBuild(t, filepath.Join(scenariosDir(), "sensitive_old"), "1.0.0")
	newB := loadBuild(t, filepath.Join(scenariosDir(), "sensitive_new"), "1.0.0")
	for _, out := range []string{
		pipeline.RenderFingerprints(oldB),
		pipeline.RenderFingerprints(newB),
		pipeline.RenderDiff(pipeline.Compare(oldB, newB)),
	} {
		if strings.Contains(out, "SECRET-AAA") || strings.Contains(out, "SECRET-BBB") {
			t.Fatalf("sensitive literal leaked: %s", out)
		}
	}
	changes := changeClasses(oldB, newB)
	if changes["vault.Token"] != "inline_const" {
		t.Fatalf("sensitive const change class = %s", changes["vault.Token"])
	}
}
