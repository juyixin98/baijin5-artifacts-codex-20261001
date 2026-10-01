package runtime_test

import (
	"testing"

	"genstatemachine/internal/frontend"
	"genstatemachine/internal/ir"
	"genstatemachine/internal/runtime"
)

func compileProg(t *testing.T, src string) (*frontend.Program, *ir.Program) {
	t.Helper()
	fp, err := frontend.Parse(src)
	if err != nil {
		t.Fatal(err)
	}
	if err := frontend.Validate(fp); err != nil {
		t.Fatal(err)
	}
	ip, err := ir.Compile(fp)
	if err != nil {
		t.Fatal(err)
	}
	return fp, ip
}

func TestSmokeBoth(t *testing.T) {
	src := "gen g {\n var i = 0;\n while (i < 3) {\n yield i;\n i = i + 1;\n }\n}"
	fp, ip := compileProg(t, src)
	m := runtime.NewMachine(ip.Gens["g"], 1000)
	d := runtime.NewDirect(fp.Gens[0])
	for k := 0; k < 3; k++ {
		mo := m.Next()
		do := d.Next()
		if mo.Kind != runtime.OutYielded || do.Kind != runtime.OutYielded {
			t.Fatalf("kind %v %v", mo, do)
		}
		if mo.Value.I != int64(k) || do.Value.I != int64(k) {
			t.Fatalf("val %d m=%d d=%d", k, mo.Value.I, do.Value.I)
		}
	}
	if m.Next().Kind != runtime.OutExhausted {
		t.Fatal("machine exhaust")
	}
	if d.Next().Kind != runtime.OutExhausted {
		t.Fatal("direct exhaust")
	}
}
